"""批量采集目录的可搬移离线报告；逐次处理，不合并未校准的时钟域。"""
import json
import os
import re
import shutil
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

from .semantic import decode_capture, render
from .chrome_trace import trace_file, write_capture

SCHEMA = 'akl.semantic.batch.v1'


def export_batch(root, output, mapping, sources, clock_mhz=None, cycle_range=None):
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
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.akl-batch-', dir=output.parent) as temporary:
        stage = Path(temporary)/'result'
        stage.mkdir()
        (stage/'sources').mkdir()
        for i, source in enumerate(sources):
            shutil.copyfile(source, stage/'sources'/f'{i}_{Path(source).name}')
        (stage/'event-map.json').write_text(json.dumps(mapping, ensure_ascii=False, indent=2))
        runs, totals = [], defaultdict(lambda: dict(count=0, intervals=0, sum_cycle=0, min_cycle=None, max_cycle=None))
        with (stage/'events.jsonl').open('w') as combined, trace_file(stage/'trace.json') as emit:
            for folder, (rank, pid, launch) in captures:
                relative = folder.relative_to(root).as_posix()
                destination = stage/'runs'/relative
                destination.mkdir(parents=True)
                run = dict(id=relative, rank=rank, pid=pid, launch=launch, status='error', warnings=[])
                try:
                    for name in ('trace.bin', 'capture.json'):
                        shutil.copyfile(folder/name, destination/name)
                    meta, events, warnings = decode_capture(folder, mapping)
                    if meta['rank'] != rank:
                        raise ValueError('目录 rank 与 capture.json 不一致')
                    render(folder, meta, events, warnings, clock_mhz, cycle_range)
                    for name in ('semantic.html', 'semantic.svg', 'semantic.jsonl', 'counts.json', 'trace.json'):
                        shutil.copyfile(folder/name, destination/name)
                    # 以相对路径识别采集，保留不同实验子目录内相同的 launch 名称。
                    for event in events:
                        combined.write(json.dumps(dict(meta, capture_id=relative, pid=pid, launch=launch, **event), ensure_ascii=False)+'\n')
                    blocks = defaultdict(list)
                    for event in events:
                        blocks[(event['block'], event['subblock'])].append(event)
                    spans = [int(lane[-1]['tick'])-int(lane[0]['tick']) for lane in blocks.values()]
                    for lane in blocks.values():
                        for i, event in enumerate(lane):
                            stat = totals[(rank, event['event_id'])]
                            stat['count'] += 1
                            if i+1 < len(lane):
                                delta = int(lane[i+1]['tick'])-int(event['tick'])
                                stat['intervals'] += 1
                                stat['sum_cycle'] += delta
                                stat['min_cycle'] = delta if stat['min_cycle'] is None else min(stat['min_cycle'], delta)
                                stat['max_cycle'] = delta if stat['max_cycle'] is None else max(stat['max_cycle'], delta)
                    run.update(status='ok', device=meta['device'], events=len(events), blocks=meta['blocks'],
                               warnings=warnings, min_block_cycle=str(min(spans)), max_block_cycle=str(max(spans)),
                               html=quote((destination.relative_to(stage)/'semantic.html').as_posix(), safe='/'))
                    write_capture(emit, relative, len(runs)+1, meta, events, warnings, clock_mhz)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    run['error'] = str(error)
                    emit(dict(ph='i', s='p', name='capture error', pid=len(runs)+1, tid=0, ts=0, args=run.copy()))
                runs.append(run)
                print(f"[{len(runs)}/{len(captures)}] {relative}: {run['status']}", flush=True)
        # 汇总整数也用十进制字符串，避免浏览器读取 >2^53 的累计值时丢精度。
        stats = [dict(rank=rank, event_id=key, path=mapping[key], **{
            k: str(v) if v is not None else None for k, v in stat.items()})
            for (rank, key), stat in sorted(totals.items())]
        report = dict(schema=SCHEMA, clock_mhz=clock_mhz, cycle_range=cycle_range, runs=runs, stats=stats)
        (stage/'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        data = json.dumps(report, ensure_ascii=False).replace('<', '\\u003c')
        page = Path(__file__).with_name('batch.html').read_text().replace('<!--REPORT-->', data)
        (stage/'index.html').write_text(page)
        with zipfile.ZipFile(stage/'result.zip', 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for path in sorted(stage.rglob('*')):
                if path.is_file() and path != stage/'result.zip':
                    archive.write(path, Path('result')/path.relative_to(stage))
        # 完整生成后才替换已有派生报告；输入采集与无关目录始终保留。
        backup = Path(temporary)/'previous'
        if output.exists():
            output.rename(backup)
        try:
            stage.rename(output)
        except OSError:
            if backup.exists():
                backup.rename(output)
            raise
    failed = sum(run['status'] != 'ok' for run in runs)
    print(f"汇总：成功 {len(runs)-failed}，失败 {failed}；{output/'index.html'}；下载 {output/'result.zip'}")
    return failed
