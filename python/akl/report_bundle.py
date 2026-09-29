"""可搬移原始实验包与离线报告；输入只读，结果成功后一次发布。"""
import argparse
import csv
import hashlib
import html
import json
import math
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from .semantic import capture_signature, decode_capture, event_map, load_event_map, render


def save_event_map(sources, output):
    data = dict(schema='akl.event-map.v1', events=event_map(sources),
                source_sha256=[hashlib.sha256(p.read_bytes()).hexdigest() for p in sources])
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def pack(root, output, latency=None, source=None, mapping=None):
    """只打包原始输入，不重复加入 HTML、图片和大型汇总 JSON。"""
    root, output = root.resolve(), output.resolve()
    if output.exists():
        raise ValueError('压缩包已存在，请换一个文件名')
    captures = sorted(root.rglob('capture.json'))
    entries = []
    for meta in captures:
        for name in ('capture.json', 'trace.bin'):
            path = meta.parent / name
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError(f'采集不完整或包含符号链接：{path}')
            entries.append((path, 'captures/' + path.relative_to(root).as_posix()))
    if latency:
        entries.append((latency, 'latency/' + latency.name))
    if not entries:
        raise ValueError('没有原始采集或 latency CSV；请在解析清理原始文件之前打包')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.akl-pack-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'experiment.zip'
        if source:
            mapping = Path(temporary) / 'event_map.json'
            save_event_map(source, mapping)
        if mapping:
            load_event_map(mapping)
            entries.append((mapping, 'event_map.json'))
        # 检查文件身份和大小，避免将仍在写入的多 rank 采集打成半包。
        signatures = {p: capture_signature(p.parent) for p in captures}
        before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p, _ in entries}
        with zipfile.ZipFile(stage, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for path, name in entries:
                archive.write(path, name)
        if any(before[p] != (p.stat().st_size, p.stat().st_mtime_ns) for p, _ in entries) or any(
                value != capture_signature(p.parent) for p, value in signatures.items()):
            raise ValueError('采集仍在变化，请等待所有 rank 结束后重新打包')
        if output.exists():
            raise ValueError('压缩包已存在，请换一个文件名')
        stage.rename(output)
    return output


def extract(archive, destination, max_bytes=32 * 1024**3):
    """拒绝跨平台路径逃逸；限制展开大小，且不解压包内的可执行文件。"""
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        if len(infos) > 100_000 or sum(i.file_size for i in infos) > max_bytes:
            raise ValueError('压缩包展开后超过 32 GiB 或 100000 个文件，请按实验拆包')
        seen, selected = set(), []
        for info in infos:
            parts = PurePosixPath(info.filename).parts
            if (info.orig_filename != info.filename or not parts or
                    info.filename.startswith('/') or '\\' in info.filename or
                    any(p in ('.', '..') or ':' in p or p.endswith((' ', '.')) or
                        re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?', p) for p in parts) or
                    stat.S_ISLNK(info.external_attr >> 16)):
                raise ValueError(f'压缩包包含不安全路径：{info.filename}')
            key = '/'.join(parts).casefold()
            if key in seen:
                raise ValueError('压缩包有重复或仅大小写不同的路径')
            seen.add(key)
            if (not info.is_dir() and '__MACOSX' not in parts and
                    (parts[-1] in ('capture.json', 'trace.bin', 'event_map.json') or
                     parts[-1].lower().endswith('.csv'))):
                selected.append(info)
        size = sum(i.file_size for i in selected)
        if shutil.disk_usage(destination).free < size + 256 * 1024**2:
            raise ValueError('输出磁盘空间不足以展开实验包')
        for info in selected:
            target = destination.joinpath(*PurePosixPath(info.filename).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, target.open('xb') as stream:
                shutil.copyfileobj(source, stream, length=1024 * 1024)


def convert(input_path, output, jobs=4, clock_mhz=None, progress=print):
    input_path, output = Path(input_path).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('结果目录已存在，请选择新目录；已有报告不会被覆盖')
    if type(jobs) is not int or not 1 <= jobs <= 64:
        raise ValueError('并行数须为 1–64')
    if clock_mhz is not None and (not math.isfinite(clock_mhz) or clock_mhz <= 0):
        raise ValueError('cycle 频率必须为有限正数')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.akl-report-', dir=output.parent) as temporary:
        incoming, stage = Path(temporary)/'input', Path(temporary)/'report'
        incoming.mkdir()
        stage.mkdir()
        progress('正在读取实验包…')
        if input_path.suffix.lower() == '.csv':
            shutil.copyfile(input_path, incoming/input_path.name)
        else:
            extract(input_path, incoming)
        mapping = None
        for path in sorted(incoming.rglob('event_map.json')):
            names = load_event_map(path)
            if mapping is None:
                mapping = {}
            for key, parts in names.items():
                if key in mapping and mapping[key] != parts:
                    raise ValueError('多份事件名称映射冲突，请按实验拆包')
                mapping[key] = parts
        notes, links, images = [], [], []
        captures = sorted(incoming.rglob('capture.json'))
        binaries = set(incoming.rglob('trace.bin'))
        if binaries != {p.parent/'trace.bin' for p in captures}:
            raise ValueError('trace.bin 必须与原始 capture.json 一起打包；后者记录容量、rank 和 block 数，不能省略')
        if captures:
            if mapping is None:
                notes.append('旧包缺少 event_map.json：时间线和耗时可查看，模块名称显示原始 event ID。')
            progress(f'正在解析 {len(captures)} 份采集（最多 {jobs} 个进程）…')
            # 保留原有 rank/launch 目录语义；任意命名的单次采集独立导出，不伪造 launch。
            standard = all(re.fullmatch(r'rank\d+-pid\d+-launch\d+', p.parent.name) for p in captures)
            if standard:
                from .batch import export_batch
                if export_batch(incoming, stage/'trace', mapping, [], clock_mhz=clock_mhz, jobs=jobs):
                    failures = [r for p in (stage/'trace').rglob('summary.json')
                                for r in json.loads(p.read_text(encoding='utf-8')).get('runs', []) if r['status']=='error']
                    raise ValueError('采集解析失败：' + '; '.join(r['id']+': '+r['error'] for r in failures[:5]))
                links.append(('按 launch / rank 查看 Trace', 'trace/index.html'))
            else:
                for i, meta_path in enumerate(captures):
                    target = stage/'trace'/str(i)
                    target.mkdir(parents=True)
                    meta, events, warnings = decode_capture(meta_path.parent, mapping)
                    render(target, meta, events, warnings, clock_mhz, intermediates=False)
                    links.append((f'Rank {meta["rank"]} · {meta_path.parent.relative_to(incoming)}',
                                  f'trace/{i}/semantic.html'))
        for path in sorted(p for p in incoming.rglob('*') if p.suffix.lower() == '.csv'):
            with path.open(encoding='utf-8-sig', newline='') as stream:
                fields = set(next(csv.reader(stream), []))
            if not {'rank', 'iteration', 'elapsed_us'} <= fields:
                notes.append(f'未识别的 CSV 已跳过：{path.relative_to(incoming)}')
                continue
            from .latency_plot import load_latency_rows, plot_latency
            progress(f'正在绘制延迟 / 带宽：{path.relative_to(incoming)}…')
            target = stage/'latency'/path.relative_to(incoming).with_suffix('')
            target.mkdir(parents=True)
            plot_latency(load_latency_rows(path), path, target/'dispatch_latency.png', last_n=0)
            for png in sorted(target.glob('*.png')):
                images.append((str(path.relative_to(incoming))+' · '+png.stem, png.relative_to(stage).as_posix()))
        if not links and not images:
            raise ValueError('包内没有可解析的 trace 或 latency CSV（需要 rank、iteration、elapsed_us 列）')
        cards = ''.join(f'<li><a href="{quote(url)}">{html.escape(label)}</a></li>' for label, url in links)
        cards += ''.join(f'<h2>{html.escape(label)}</h2><a href="{quote(url)}" download>下载 PNG</a>'
                         f'<img loading="lazy" src="{quote(url)}" alt="{html.escape(label, quote=True)}">'
                         for label, url in images)
        (stage/'index.html').write_text('<!doctype html><html lang="zh"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"><title>AKL 实验报告</title>'
            '<style>body{font:16px system-ui;max-width:1200px;margin:32px auto;padding:0 24px;color:#183047}'
            'img{max-width:100%}li{margin:16px 0}.note{padding:12px;background:#fff4d6}a{color:#1452a3}</style>'
            f'<h1>AKL 实验报告</h1><p>{html.escape(input_path.name)}</p>'
            '<p>输入压缩包保留。整个结果目录可离线复制；从 index.html 打开。Trace 时钟未作跨核 / 跨 rank 校准。</p>'
            + ''.join(f'<p class="note">{html.escape(n)}</p>' for n in notes) + cards + '</html>', encoding='utf-8')
        if output.exists():
            raise ValueError('结果目录已存在，请选择新目录')
        stage.rename(output)
    progress('报告已生成；原始输入未修改。')
    return output/'index.html'


def main():
    import sys
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('pack', 'map', 'render'):
        command = sub.add_parser(name)
        if name != 'map':
            command.add_argument('input', type=Path)
        command.add_argument('--output', type=Path, required=True)
        if name != 'render':
            command.add_argument('--source', type=Path, nargs='+', required=name=='map')
        if name == 'pack':
            command.add_argument('--latency', type=Path)
            command.add_argument('--event-map', type=Path)
        if name == 'render':
            command.add_argument('--jobs', type=int, default=min(4, os.cpu_count() or 1))
            command.add_argument('--clock-mhz', type=float)
    args = parser.parse_args()
    try:
        if args.command == 'map':
            save_event_map(args.source, args.output)
        elif args.command == 'pack':
            if args.source and args.event_map:
                parser.error('--source 与 --event-map 不能同时使用')
            print(pack(args.input, args.output, args.latency, args.source, args.event_map))
        else:
            print(convert(args.input, args.output, args.jobs, args.clock_mhz))
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
