"""按实际 UB 规划容量/批量扫描，以两轮原始采样检查吞吐平台候选。"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from .datacopy import CopyCase
from .cases import WORDS


def plan(ub_bytes, mode='small', ring_bytes=64 * 1024 * 1024, windows=1, min_loops=128):
    if type(ub_bytes) is not int or not WORDS * 8 + 32 <= ub_bytes <= 256 * 1024 * 1024:
        raise ValueError('请提供目标设备实际 UB 字节容量')
    if mode not in ('small', 'ring') or not 32 <= ring_bytes <= 255 * 1024 * 1024:
        raise ValueError('工作集参数越界')
    if windows not in (1, 2) or type(min_loops) is not int or not windows <= min_loops <= 100000:
        raise ValueError('windows/min_loops 越界')
    cases, skipped = [], []
    for batch in (1, 2, 4, 8, 16, 32, 64):
        maximum = min((ub_bytes - WORDS * 8) // (batch * windows) // 32 * 32, 65535 * 32)
        sizes = sorted({32 * 2 ** n for n in range(16) if 32 * 2 ** n <= maximum}
                       | {n for n in (14336, maximum) if 32 <= n <= maximum})
        for size in sizes:
            # ring 不够大时跳过，不能把逐渐变大的小工作集接成同一条大环曲线。
            slots = batch * windows if mode == 'small' else max(batch * windows, (ring_bytes + size * batch - 1) // (size * batch) * batch)
            loops = max(min_loops, 2 * slots // batch)
            if slots > 65536 or loops > 100000 or slots * size > 256 * 1024 * 1024:
                skipped.append(dict(batch=batch, bytes=size, reason='ring 超出槽位/循环/GM 上限'))
                continue
            for direction in ('GM_UB', 'UB_GM'):
                case = CopyCase(f'{mode}_{direction}_{size}_b{batch}', direction=direction,
                                block_bytes=size, batch=batch, slots=slots, loops=loops, windows=windows)
                case.params()
                cases.append(asdict(case))
    if not cases:
        raise ValueError('没有合法配置')
    return dict(schema='akl.datacopy.sweep.v1', ub_bytes=ub_bytes, mode=mode, windows=windows, min_loops=min_loops,
                requested_ring_bytes=ring_bytes if mode == 'ring' else None,
                conditions=f"one AIV; uint32; DataCopy_params; {'batch' if windows == 1 else 'two-window'} completion; default cache policy",
                cases=cases, skipped=skipped)


def plateau(series, tolerance=.10):
    """保守的实验筛选规则，不是统计置信区间或硬件上限证明。"""
    tail = sorted(series, key=lambda r: r['bytes'])[-3:]
    if len(tail) < 3 or tail[-1]['bytes'] < 2 * tail[0]['bytes']:
        return dict(status='insufficient_range', tail_bytes=[r['bytes'] for r in tail])
    bandwidths = [v for r in tail for v in r['repeat_GBps']]
    spread = max(bandwidths) / min(bandwidths) - 1
    noisy = any(v > 1 + tolerance for r in tail for v in r['p95_over_p50'])
    enough_repeats = all(len(r['repeat_GBps']) >= 2 for r in tail)
    status = 'candidate_plateau' if enough_repeats and not noisy and spread <= tolerance else 'not_observed'
    return dict(status=status, tail_bytes=[r['bytes'] for r in tail], spread=spread,
                noisy=noisy, repeated=enough_repeats, tolerance=tolerance)


def report(spec, runs, output):
    from .datacopy_report import analyse, identity
    import numpy as np
    if len(runs) < 2 or len({p.resolve() for p in runs}) != len(runs):
        raise ValueError('需要至少两次独立运行，不能重复使用同一个目录')
    if spec.get('schema') != 'akl.datacopy.sweep.v1':
        raise ValueError('未知扫描计划')
    # 旧计划未记录windows/min_loops；默认行为保持一致，仍逐配置核对。
    spec = dict(spec, windows=spec.get('windows', 1), min_loops=spec.get('min_loops', 128),
                cases=[asdict(CopyCase(**c)) for c in spec['cases']])
    if spec != plan(spec['ub_bytes'], spec['mode'], spec['requested_ring_bytes'] or 64 * 1024 * 1024, spec['windows'], spec['min_loops']):
        raise ValueError('扫描计划与容量/工作集规则不符')
    cases = {c['name']: c for c in spec['cases']}
    if len(cases) != len(spec['cases']) or not cases:
        raise ValueError('计划为空或重名')
    tables, manifests, hashes = [], [], []
    for run in runs:
        m = json.loads((run / 'manifest.json').read_text())
        if {c['case']['name']: asdict(CopyCase(**c['case'])) for c in m['cases']} != cases:
            raise ValueError('运行配置与扫描计划不符')
        if m['hardware']['ub_bytes'] != spec['ub_bytes']:
            raise ValueError('计划 UB 容量与实际设备不符')
        if m['arguments']['samples'] < 20:
            raise ValueError('平台检查每点需要至少 20 个计时样本')
        manifests.append(m)
        hashes.append(hashlib.sha256((run / 'manifest.json').read_bytes()).hexdigest())
        tables.append({r['name']: r for r in analyse(run)})
    if len(set(hashes)) != len(hashes) or len({m['run_id'] for m in manifests}) != len(manifests):
        raise ValueError('两轮必须有独立的运行标识和原始证据')
    if any(identity(m) != identity(manifests[0]) or
           m['build']['library_sha256'] != manifests[0]['build']['library_sha256'] for m in manifests[1:]):
        raise ValueError('两轮环境或测量内核不一致')
    points = []
    for name, c in cases.items():
        rows = [table[name] for table in tables]
        points.append(dict(name=name, direction=c['direction'], batch=c['batch'], bytes=c['block_bytes'],
                           working_set_bytes=rows[0]['working_set_bytes'], loops=c['loops'],
                           repeat_GBps=[r['payload_GBps_at_p50'] for r in rows],
                           p95_over_p50=[r['p95_us_per_call'] / r['p50_us_per_call'] for r in rows]))
    groups = []
    for direction in ('GM_UB', 'UB_GM'):
        for batch in sorted({p['batch'] for p in points}):
            series = sorted((p for p in points if p['direction'] == direction and p['batch'] == batch), key=lambda p: p['bytes'])
            if series:
                groups.append(dict(direction=direction, batch=batch, plateau=plateau(series), points=series))
    result = dict(schema='akl.datacopy.sweep-report.v1', conclusion='no_hardware_upper_bound_claim',
                  mode=spec['mode'], windows=spec['windows'], min_loops=spec['min_loops'], manifests_sha256=hashes, groups=groups)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'saturation.json').write_text(json.dumps(result, indent=2))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.6))
    lines = ['# 单 AIV 批量完成吞吐扫描', '',
             '每条线固定 batch；点为各轮 p50 吞吐的中位数，阴影为各轮范围。原始样本已逐条核对。', '',
             '末尾三个尺寸跨度至少 2 倍、各轮带宽总差异不超过 10%、每点 p95/p50 不超过 1.10，才标记“平台候选”。该筛选规则不能证明硬件峰值；没有通过就继续扩大范围或检查发射/同步瓶颈。', '',
             f"windows={spec['windows']}：1为每批完成，2为复用前等待的双窗口流水。平台可能来自该实现的发射或同步限制。small 为重复小工作集，ring 为固定目标容量的环，均未证明绕过缓存。", '',
             '| 方向 | batch | 末端尺寸 B | 检查结果 |', '|---|---:|---|---|']
    for ax, direction in zip(axes, ('GM_UB', 'UB_GM')):
        for g in (g for g in groups if g['direction'] == direction):
            points = g['points']
            x = [p['bytes'] for p in points]
            y = [np.median(p['repeat_GBps']) for p in points]
            line, = ax.plot(x, y, 'o-', ms=3, label=f"batch={g['batch']}")
            ax.fill_between(x, [min(p['repeat_GBps']) for p in points], [max(p['repeat_GBps']) for p in points], alpha=.12, color=line.get_color())
            lines.append(f"| {direction} | {g['batch']} | {g['plateau']['tail_bytes']} | {g['plateau']['status']} |")
        ax.set(xscale='log', xlabel='Bytes per DataCopy call', ylabel='Effective payload GB/s', title=direction)
        ax.set_ylim(bottom=0); ax.grid(alpha=.2); ax.legend(fontsize=8)
    fig.suptitle(f"{manifests[0]['profile']['soc']} | one AIV | {spec['mode']} | windows={spec['windows']}; no peak claim")
    fig.tight_layout()
    for extension in ('png', 'svg'):
        fig.savefig(output / f'throughput-sweep.{extension}', dpi=170)
    plt.close(fig)
    lines += ['', '![吞吐扫描](throughput-sweep.png)', '', '精确工作集、循环数、每轮带宽和平台检查结果见 saturation.json。']
    (output / 'report.md').write_text('\n'.join(lines) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('plan')
    p.add_argument('--ub-bytes', type=int, required=True)
    p.add_argument('--mode', choices=('small', 'ring'), default='small')
    p.add_argument('--ring-mib', type=int, default=64)
    p.add_argument('--windows', type=int, choices=(1, 2), default=1)
    p.add_argument('--min-loops', type=int, default=128)
    p.add_argument('--output', type=Path, required=True)
    r = commands.add_parser('report')
    r.add_argument('plan', type=Path)
    r.add_argument('runs', type=Path, nargs='+')
    r.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'plan':
        spec = plan(args.ub_bytes, args.mode, args.ring_mib * 1024 * 1024, args.windows, args.min_loops)
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / 'plan.json').write_text(json.dumps(spec, ensure_ascii=False, indent=2))
        (args.output / 'cases.json').write_text(json.dumps(spec['cases'], indent=2))
        # 每个 batch 的 UB 容量端点先冒烟，防止长矩阵掩盖边界错误。
        largest = {b: max(c['block_bytes'] for c in spec['cases'] if c['batch'] == b)
                   for b in {c['batch'] for c in spec['cases']}}
        smoke = [dict(c, slots=c['batch'] * args.windows, loops=2) for c in spec['cases']
                 if c['block_bytes'] == largest[c['batch']]]
        (args.output / 'smoke.json').write_text(json.dumps(smoke, indent=2))
        print(f"生成 {len(spec['cases'])} 个配置；跳过 {len(spec['skipped'])} 个容量不符组合，见 plan.json")
    else:
        report(json.loads(args.plan.read_text()), args.runs, args.output)
        print(f'原始证据核对完成：{args.output}')


if __name__ == '__main__':
    main()
