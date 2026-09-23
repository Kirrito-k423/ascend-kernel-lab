"""从原始记录生成 DataCopy 图片和带环境约束的经验基线。"""
import csv
import hashlib
import json
from pathlib import Path
import numpy as np
from .datacopy import CopyCase, SCHEMA
from .trace import decode, duration_ticks


def identity(manifest):
    profile = manifest['profile']
    build = manifest['build']
    return dict(soc=profile['soc'], topology=profile['topology'], memory_scope=profile['memory_scope'],
                clock_hz=profile['clock_hz'], npu_arch=build['npu_arch'],
                cann_sha256=hashlib.sha256(build['cann_install'].encode()).hexdigest(),
                compiler_sha256=hashlib.sha256(build['compiler'].encode()).hexdigest(),
                implementation='AscendC_MTE', aiv_count=1,
                cache_policy='default; reused ring; coldness unverified')


def analyse(root):
    root = Path(root)
    manifest = json.loads((root/'manifest.json').read_text())
    if manifest.get('schema') != SCHEMA or manifest.get('status') != 'validated':
        raise ValueError('只有完整且通过正确性验证的运行可归档')
    if not all(manifest.get(k, {}).get('observed_idle') is True for k in ('occupancy_before', 'occupancy_after')):
        raise ValueError('缺少运行前后设备空闲证据')
    for label in ('before', 'after'):
        if hashlib.sha256((root/f'occupancy-{label}.txt').read_bytes()).hexdigest() != manifest[f'occupancy_{label}']['sha256']:
            raise ValueError('设备占用证据哈希不一致')
    hz = manifest['profile']['clock_hz']
    if type(hz) is not int or hz <= 0: raise ValueError('未知时钟频率')
    rows, entries = [], []
    for record in manifest['cases']:
        case = CopyCase(**record['case'])
        if record['status'] != 'validated' or record['params'] != case.params():
            raise ValueError('case 校验/参数不一致')
        layout = case.layout()
        legacy_layout = dict(layout)
        legacy_layout.pop('api_length_unit')
        if case.api == 'DataCopy_count': legacy_layout['api_block_len'] = case.block_bytes // 32
        if record['layout'] not in (layout, legacy_layout): raise ValueError('layout 与参数不一致')
        # 首轮采集的 count 元数据展示为32B块数，kernel的真实长度始终按元素。
        # 只兼容这个可由精确params验证的展示差异；保留原manifest与raw，不改采样值。
        expected_retained = case.block_bytes*case.blocks*case.batch if case.control=='payload' else 0
        if record['expected_retained'] != expected_retained: raise ValueError('oracle 输出数量不符')
        samples = json.loads((root/case.name/'samples.json').read_text())
        if len(samples) != record['launches'] or not samples: raise ValueError('样本数量不一致')
        if len({s['launch'] for s in samples}) != len(samples): raise ValueError('launch 重复')
        plain = [s for s in samples if not s['trace'] and not s['warmup']]
        if len(plain) != manifest['arguments']['samples']: raise ValueError('plain 对照缺失')
        values = []
        for sample in samples:
            if sample['correctness'] is not True: raise ValueError('样本正确性失败')
            if sample['trace']:
                raw = np.load(root/case.name/f"trace-{sample['launch']}.npy", allow_pickle=False)
                events = decode(raw, manifest['run_id'], sample['launch'], manifest['device'], [record['expected_retained']])
                ticks = duration_ticks(events, 0)
                if str(ticks) != sample['ticks'] or ticks <= 0: raise ValueError('raw tick 不符或非正')
                if not sample['warmup']:
                    values.append(ticks * 1e6 / hz / (case.loops*case.batch))
        if len(values) != manifest['arguments']['samples']: raise ValueError('计时样本数不符')
        p50, p95 = (float(np.percentile(values, p)) for p in (50, 95))
        payload = case.block_bytes*case.blocks if case.control == 'payload' else 0
        row = dict(name=case.name, direction=case.direction, api=case.api, dtype=case.dtype,
                   block_bytes=case.block_bytes, blocks=case.blocks, gm_gap_bytes=case.gm_gap_bytes,
                   batch=case.batch, slots=case.slots, loops=case.loops, control=case.control,
                   samples=len(values), p50_us_per_call=p50, p95_us_per_call=p95,
                   min_us_per_call=min(values), max_us_per_call=max(values), std_us_per_call=float(np.std(values)),
                   payload_bytes_per_call=payload,
                   payload_GBps_at_p50=payload/p50/1000 if payload else None,
                   working_set_bytes=case.layout()['gm_working_set_bytes'],
                   api_length_unit=layout['api_length_unit'],api_length_value=layout['api_block_len'],
                   layout_metadata_corrected=(record['layout'] != layout))
        rows.append(row)
        if payload:
            key = dict(environment=identity(manifest), case=case.signature())
            entry_id = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:20]
            entries.append(dict(id=entry_id, key=key, p50_us_per_call=p50, p95_us_per_call=p95,
                                sample_count=len(values), baseline_kind='empirical_isolated_completion',
                                evidence=dict(run=manifest['run_id'], case=case.name,
                                    library_sha256=manifest['build']['library_sha256'],
                                    manifest_sha256=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest())))
    if not rows: raise ValueError('空运行')
    (root/'summary.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    with (root/'summary.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    (root/'catalog.json').write_text(json.dumps(dict(schema='akl.datacopy.catalog.v1', entries=entries), ensure_ascii=False, indent=2))
    render(root, manifest, rows)
    return rows


def render(root, manifest, rows):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import html
    main = [r for r in rows if r['control']=='payload' and r['blocks']==1 and r['block_bytes']%32==0]
    for metric, ylabel, filename in (('p50_us_per_call','Completion time per call (us)','latency'),
                                    ('payload_GBps_at_p50','Effective payload (GB/s), one AIV','throughput')):
        fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
        for ax, direction in zip(axes, ('GM_UB','UB_GM')):
            groups = sorted({(r['api'],r['dtype'],r['batch'],r['slots'],r['loops']) for r in main if r['direction']==direction})
            for api, dtype, batch, slots, loops in groups:
                values = sorted((r for r in main if (r['direction'],r['api'],r['dtype'],r['batch'],r['slots'],r['loops']) == (direction,api,dtype,batch,slots,loops)),key=lambda r:r['block_bytes'])
                label = f"{api.replace('DataCopy','DC')} / {dtype} / B{batch} S{slots} L{loops}"
                ax.plot([r['block_bytes'] for r in values],[r[metric] for r in values],marker='o',markersize=3,label=label)
                if metric=='p50_us_per_call':
                    ax.fill_between([r['block_bytes'] for r in values],[r[metric] for r in values],[r['p95_us_per_call'] for r in values],alpha=.09)
            ax.set(xscale='log', xlabel='Payload bytes per call (log scale)', ylabel=ylabel,title=direction.replace('_',' -> '))
            ax.grid(alpha=.2); ax.legend(fontsize=6)
        fig.suptitle(f"{manifest['profile']['soc']} | one AIV | reused ring | raw timing, no subtraction")
        fig.tight_layout()
        for ext in ('png','svg'): fig.savefig(root/f'{filename}.{ext}',dpi=170)
        plt.close(fig)
    special = [r for r in rows if r['blocks']>1 or r['control']!='payload' or r['block_bytes']%32]
    if special:
        fig, ax = plt.subplots(figsize=(12,max(4,len(special)*.3)))
        ax.barh([r['name'] for r in special],[r['p50_us_per_call'] for r in special],color='#26888a')
        ax.invert_yaxis();ax.set_xlabel('Completion time per call (us), p50');ax.grid(axis='x',alpha=.2)
        fig.tight_layout()
        for ext in ('png','svg'): fig.savefig(root/f'controls-strides.{ext}',dpi=170)
        plt.close(fig)
    total = sum(r['launches'] for r in manifest['cases'])
    lines = ['# DataCopy 单 AIV 实测基线','',f"环境：{manifest['profile']['soc']} / {manifest['profile']['topology']}。{len(rows)} 个配置、{total} 次启动均通过 payload、跨步写 gap 与输出保护区检查。",
             '', '计时为循环总完成时间除以调用次数；batch=1 每次等待完成，batch>1 每批等待完成。所有值包括循环、分支、地址计算和同步，未扣除空循环。GB/s 只统计有效搬运字节，不包含地址跨度，不代表物理 HBM 带宽。',
             '', '运行前后 npu-smi 均未观察到其他 NPU 进程；不能排除采样间短暂干扰。固定工作集可能命中缓存，大环形工作集也不自动叫冷缓存。',
             '', '本目录 catalog.json 是匹配条件下的经验参考，不是理论最优值。SIMT、REG、远端 GM 和 A5 PoD 需独立实验。',
             '', '![延迟](latency.png)','', '![吞吐](throughput.png)','',
             '| 配置 | p50 μs/次 | p95 μs/次 | 有效 GB/s |','|---|---:|---:|---:|']
    if any(r['layout_metadata_corrected'] for r in rows):
        lines.insert(4, '早期原始 manifest 的 count 长度展示单位已在报告中按真实 kernel 参数修正；原始 manifest 保持不变，summary 标记 layout_metadata_corrected。原始 tick 和性能数值没有更改。')
    lines += [f"| {r['name']} | {r['p50_us_per_call']:.5f} | {r['p95_us_per_call']:.5f} | {r['payload_GBps_at_p50']:.3f} |" if r['payload_GBps_at_p50'] is not None else f"| {r['name']} | {r['p50_us_per_call']:.5f} | {r['p95_us_per_call']:.5f} | — |" for r in rows]
    (root/'report.md').write_text('\n'.join(lines)+'\n')
    table=''.join(f"<tr><td>{html.escape(r['name'])}</td><td>{r['p50_us_per_call']:.5f}</td><td>{r['p95_us_per_call']:.5f}</td><td>{r['payload_GBps_at_p50'] or 0:.3f}</td></tr>" for r in rows)
    special_html = '<section><h2>跨步与测量对照</h2><img src="controls-strides.svg"></section>' if special else ''
    body=f'''<!doctype html><meta charset="utf-8"><title>DataCopy 性能基线</title><style>body{{font:16px system-ui;color:#173148;background:#f4f7fa;max-width:1300px;margin:32px auto;padding:20px}}img{{width:100%}}td,th{{padding:8px;text-align:left;border-bottom:1px solid #ddd}}section{{background:white;padding:24px;margin:20px 0;border-radius:12px}}table{{font-size:13px;width:100%}}</style><h1>DataCopy 单 AIV 性能基线</h1><p>{len(rows)} 个配置 · {total} 次真实 NPU 启动 · {html.escape(manifest['profile']['soc'])}</p><section>每次完成延迟与批量完成吞吐分开测量。带宽只计有效 payload，未减空循环，不声称是硬件极限。<br>同芯片、版本、shape、dtype、同步和工作集才可比较。<br><a href="report.md">完整说明</a> · <a href="summary.csv">CSV</a> · <a href="catalog.json">基线目录</a> · <a href="manifest.json">环境与原始样本索引</a></section><section><h2>小 shape 看延迟</h2><img src="latency.svg"></section><section><h2>大 shape 看吞吐</h2><img src="throughput.svg"></section>{special_html}<section><h2>全部结果</h2><table><tr><th>配置</th><th>p50 μs/次</th><th>p95 μs/次</th><th>有效 GB/s（对照为0）</th></tr>{table}</table></section>'''
    (root/'report.html').write_text(body)


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('run',type=Path)
    a=p.parse_args(); print(f'已校验并出图：{len(analyse(a.run))} 个配置')

if __name__ == '__main__': main()
