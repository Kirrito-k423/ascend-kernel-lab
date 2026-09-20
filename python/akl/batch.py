"""批量采集目录的可搬移离线报告；多进程解析，不合并未校准的时钟域。"""
import json
import html
import os
import re
import shutil
import tempfile
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from functools import partial
import zipfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from .semantic import capture_signature, clean_capture, decode_capture, render
from .chrome_trace import merge_traces

SCHEMA = 'akl.semantic.batch.v1'


def discover(root, output):
    root, output = root.resolve(), output.resolve()
    if not root.is_dir() or output == root or output in root.parents:
        raise ValueError('输入须为父目录；汇总目录不能覆盖输入目录或其祖先')
    if output.exists():
        manifest = output / 'summary.json'
        if not manifest.is_file() or json.loads(manifest.read_text()).get('schema') != SCHEMA:
            raise ValueError('汇总目录已存在且不是本工具生成的报告，请更换 --output')
    captures = []
    for parent, dirs, _ in os.walk(root):
        manifest = Path(parent)/'summary.json'
        try:
            if manifest.is_file() and json.loads(manifest.read_text()).get('schema') == SCHEMA:
                dirs.clear()  # 更换 --output 后，仍不递归分析旧报告里的原始数据副本。
                continue
        except (OSError, ValueError):
            pass
        dirs[:] = sorted(d for d in dirs if not (Path(parent)/d).is_symlink()
                         and (Path(parent)/d).resolve() != output and not d.startswith('.akl-batch-'))
        for name in list(dirs):
            match = re.fullmatch(r'rank(\d+)-pid(\d+)-launch(\d+)', name)
            if match:
                folder = Path(parent)/name
                if folder in output.parents:
                    raise ValueError('汇总目录不能放在某个采集目录内')
                captures.append((folder, tuple(map(int, match.groups()))))
                dirs.remove(name)
    if not captures:
        raise ValueError('父目录下没有 rank*-pid*-launch* 采集目录')
    captures.sort(key=lambda item: (str(item[0].parent.relative_to(root)), *item[1]))
    return captures


def export_capture(task, root, stage, mapping, clock_mhz, cycle_range, keep_intermediates):
    index, (folder, (rank, pid, launch)) = task
    relative = folder.relative_to(root)
    group = Path('launches')/relative.parent/f'launch{launch}'
    destination = stage/group/'runs'/folder.name
    run = dict(id=relative.as_posix(), rank=rank, pid=pid, launch=launch, status='error', warnings=[])
    totals = defaultdict(lambda: dict(count=0, intervals=0, sum_cycle=0, min_cycle=None, max_cycle=None))
    signature = None
    try:
        signature = capture_signature(folder)
        meta, events, warnings = decode_capture(folder, mapping)
        if meta['rank'] != rank:
            raise ValueError('目录 rank 与 capture.json 不一致')
        destination.mkdir(parents=True)
        render(destination, meta, events, warnings, clock_mhz, cycle_range,
               intermediates=keep_intermediates, trace_pid=index+1, capture_id=relative.as_posix())
        if capture_signature(folder) != signature:
            raise ValueError('解析期间采集已被修改，请等待写入完成')
        blocks = defaultdict(list)
        for event in events:
            blocks[(event['block'], event['subblock'])].append(event)
        spans = [int(lane[-1]['tick'])-int(lane[0]['tick']) for lane in blocks.values()]
        for lane in blocks.values():
            for i, event in enumerate(lane):
                stat = totals[event['event_id']]
                stat['count'] += 1
                if i+1 < len(lane):
                    delta = int(lane[i+1]['tick'])-int(event['tick'])
                    stat['intervals'] += 1
                    stat['sum_cycle'] += delta
                    stat['min_cycle'] = delta if stat['min_cycle'] is None else min(stat['min_cycle'], delta)
                    stat['max_cycle'] = delta if stat['max_cycle'] is None else max(stat['max_cycle'], delta)
        run.update(status='ok', device=meta['device'], events=len(events), blocks=meta['blocks'],
                   warnings=warnings, min_block_cycle=str(min(spans)), max_block_cycle=str(max(spans)),
                   html=quote((Path('runs')/folder.name/'semantic.html').as_posix(), safe='/'))
    except (OSError, ValueError, KeyError, TypeError) as error:
        run['error'] = str(error)
        totals.clear()
        if destination.exists():
            shutil.rmtree(destination)  # 仅丢弃当前临时报告；失败的原始采集始终保留。
    return group, run, dict(totals), signature


def export_group(output, runs, totals, mapping, clock_mhz, cycle_range, zip_launches):
    output.mkdir(parents=True, exist_ok=True)
    stats = [dict(rank=rank, event_id=key, path=mapping[key], **{
        k: str(v) if v is not None else None for k, v in stat.items()})
        for (rank, key), stat in sorted(totals.items())]
    report = dict(schema=SCHEMA, launch=runs[0]['launch'], clock_mhz=clock_mhz,
                  cycle_range=cycle_range, runs=runs, stats=stats)
    (output/'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    data = json.dumps(report, ensure_ascii=False).replace('<', '\\u003c')
    page = Path(__file__).with_name('batch.html').read_text().replace('<!--REPORT-->', data)
    page = page.replace('<!--ZIP-->', '<a href="result.zip" download>下载本 launch ZIP</a>' if zip_launches else '')
    (output/'index.html').write_text(page)
    merge_traces((output/'runs'/Path(run['id']).name/'trace.json' for run in runs if run['status']=='ok'),
                 output/'trace.json')
    if zip_launches:
        with zipfile.ZipFile(output/'result.zip', 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for path in sorted(output.rglob('*')):
                if path.is_file() and path != output/'result.zip':
                    archive.write(path, Path('result')/path.relative_to(output))


def export_batch(root, output, mapping, sources, clock_mhz=None, cycle_range=None,
                 jobs=64, keep_intermediates=False, zip_launches=False, last_launch=False):
    root, output = root.resolve(), output.resolve()
    captures = discover(root, output)
    all_captures = captures
    skipped = []
    if last_launch:
        latest, ranks = {}, defaultdict(set)
        for folder, (rank, pid, launch) in captures:
            latest[folder.parent] = max(latest.get(folder.parent, launch), launch)
            ranks[folder.parent].add(rank)
        captures = [(folder, ids) for folder, ids in captures if ids[2] == latest[folder.parent]]
        for parent, expected in ranks.items():
            if {ids[0] for folder, ids in captures if folder.parent == parent} != expected:
                raise ValueError(f'{parent}: 最后 launch 缺少部分 rank，请等待采集完成')
        selected = {folder for folder, _ in captures}
        # 被跳过的旧轮次不解码；记录文件身份，仅在最终报告成功后清理。
        for folder, ids in all_captures:
            if folder not in selected:
                meta = json.loads((folder/'capture.json').read_text())
                if meta.get('schema') != 'akl.semantic.v1' or meta.get('rank') != ids[0]:
                    raise ValueError(f'{folder}: 旧采集协议/rank 不匹配，保留输入')
                skipped.append((folder, capture_signature(folder)))
    if type(jobs) is not int or jobs <= 0:
        raise ValueError('jobs 必须是正整数')
    workers = min(jobs, len(captures))
    print(f'批量解析：{len(captures)} 次采集，{workers} 个进程', flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    signatures = []
    with tempfile.TemporaryDirectory(prefix='.akl-batch-', dir=output.parent) as temporary:
        stage = Path(temporary)/'result'
        stage.mkdir()
        groups = defaultdict(lambda: ([], {}))
        worker = partial(export_capture, root=root, stage=stage, mapping=mapping, clock_mhz=clock_mhz,
                         cycle_range=cycle_range, keep_intermediates=keep_intermediates)
        # worker 只返回小型统计；事件和图形留在文件中，不在主进程堆积或重复 pickle。
        pool = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('spawn')) if workers > 1 else nullcontext()
        with pool as executor:
            results = executor.map(worker, enumerate(captures)) if executor else map(worker, enumerate(captures))
            for group, run, totals, signature in results:
                runs, stats = groups[group]
                runs.append(run)
                for key, value in totals.items():
                    prior = stats.setdefault((run['rank'], key), dict(count=0, intervals=0, sum_cycle=0, min_cycle=None, max_cycle=None))
                    for name in ('count', 'intervals', 'sum_cycle'):
                        prior[name] += value[name]
                    for name, combine in (('min_cycle', min), ('max_cycle', max)):
                        if value[name] is not None:
                            prior[name] = value[name] if prior[name] is None else combine(prior[name], value[name])
                signatures.append(signature)
                print(f"[{len(signatures)}/{len(captures)}] {run['id']}: {run['status']}", flush=True)
        launches = []
        for relative, (runs, totals) in sorted(groups.items(), key=lambda item: (str(item[0].parent), item[1][0][0]['launch'])):
            export_group(stage/relative, runs, totals, mapping, clock_mhz, cycle_range, zip_launches)
            ranks = [run['rank'] for run in runs]
            warning = '同一 rank 有多个 PID；请确认这些采集属于同一轮实验' if len(set(ranks)) != len(ranks) else ''
            launches.append(dict(experiment=relative.parent.relative_to('launches').as_posix(), launch=runs[0]['launch'],
                                 captures=len(runs), ranks=len(set(ranks)), failed=sum(r['status']!='ok' for r in runs),
                                 warning=warning, directory=quote(relative.as_posix(), safe='/')))
        report = dict(schema=SCHEMA, kind='launch-index', launches=launches, jobs=workers,
                      keep_intermediates=keep_intermediates, zip_launches=zip_launches, last_launch=last_launch,
                      skipped_captures=len(skipped))
        (stage/'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        rows = []
        for entry in launches:
            link = html.escape(entry['directory'], quote=True)
            archive = f' · <a href="{link}/result.zip" download>独立 ZIP</a>' if zip_launches else ''
            rows.append(f"<tr><td>{html.escape(entry['experiment'])}</td><td>{entry['launch']}</td>"
                        f"<td>{entry['ranks']}</td><td>{entry['captures']}</td><td>{entry['failed']}</td>"
                        f'<td><a href="{link}/index.html">HTML</a> · <a href="{link}/trace.json" download>Chrome JSON</a>'
                        f'{archive}</td><td>{html.escape(entry["warning"])}</td></tr>')
        (stage/'index.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>按 launch 查看采集</title>'
            '<style>body{font:14px system-ui;margin:24px;color:#183047}table{border-collapse:collapse;width:100%}'
            'td,th{padding:10px;text-align:left;border-bottom:1px solid #ddd}a{color:#1452a3}</style>'
            '<h1>按 launch 查看采集</h1><p>每个 launch 独立汇总各 rank 的 HTML 和 Chrome Trace JSON。'
            '索引不加载时间线数据；同号 launch 仅按目录编号归组，不代表跨 rank 时钟已校准。</p>'
            '<p>默认仅保留 result 内的一套报告，不生成压缩包；全部成功后清理原始采集。'
            '重新解析须事先使用 --keep-intermediates 保留原始数据。'
            '<a href="summary.json" download>索引 JSON</a></p>'
            '<table><thead><tr><th>实验目录</th><th>Launch</th><th>Rank 数</th><th>采集数</th><th>失败数</th>'
            '<th>查看 / 下载</th><th>提示</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></html>')
        backup = Path(temporary)/'previous'
        if output.exists():
            output.rename(backup)
        try:
            stage.rename(output)
        except OSError:
            if backup.exists():
                backup.rename(output)
            raise
    failed = sum(entry['failed'] for entry in launches)
    # 先完成全部报告并替换成功，再清理输入；部分失败时整批输入保留以便重试。
    if not failed and not keep_intermediates:
        cleanup = [(folder, signature) for (folder, _), signature in zip(captures, signatures)] + skipped
        if any(capture_signature(folder) != signature for folder, signature in cleanup):
            raise ValueError('采集在解析后被修改；报告已保存，输入保留，请重新解析')
        for folder, signature in cleanup:
            clean_capture(folder, signature, remove_reports=True)
    print(f"按 launch 汇总：{len(launches)} 组，失败 {failed}；{output/'index.html'}")
    return failed
