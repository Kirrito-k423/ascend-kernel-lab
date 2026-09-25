"""将内网测量压缩为小于5MB的ZIP；保留原始trace，图片可在接收端重画。"""
import argparse
import json
from pathlib import Path
import zipfile

from .datacopy import CopyCase, SCHEMA


def bundle(runs, output, limit=5_000_000):
    runs = [Path(r).resolve() for r in runs]
    output = Path(output)
    if not runs or len({r.name for r in runs}) != len(runs):
        raise ValueError('请选择名称不同的结果目录')
    files, index = [], []
    for root in runs:
        manifest = json.loads((root / 'manifest.json').read_text())
        if manifest.get('schema') != SCHEMA:
            raise ValueError('未知结果协议')
        validated = manifest.get('status') == 'validated'
        paths = [root / n for n in ('manifest.json', 'source.tar.gz', 'occupancy-before.txt', 'occupancy-after.txt')]
        if validated and not all(p.is_file() for p in paths):
            raise ValueError('有效结果缺少环境/源码证据')
        paths += [root / 'error.txt']
        for record in manifest['cases']:
            case = CopyCase(**record['case'])
            case.params()  # 名称校验同时禁止路径逃逸。
            folder = root / case.name
            samples = folder / 'samples.json'
            if validated and not samples.is_file():
                raise ValueError('有效结果缺少samples.json')
            if samples.is_file():
                rows = json.loads(samples.read_text())
                traces = [folder / f"trace-{s['launch']}.npy" for s in rows if s['trace']]
                if not all(p.is_file() for p in traces):
                    raise ValueError('原始trace缺失，不生成不完整证据包')
                paths += [samples, *traces]
            else:
                paths += sorted(folder.glob('trace-*.npy'))
        for path in paths:
            if path.is_file():
                if not path.resolve().is_relative_to(root):
                    raise ValueError('拒绝打包结果目录外的文件')
                files.append((path, f'{root.name}/{path.relative_to(root)}'))
        index.append(dict(run=root.name, status=manifest['status'], cases=len(manifest['cases']),
                          failed_outputs_retained_locally=[str(p.relative_to(root)) for p in root.glob('*/failed-output-*.npz')]))
    output.parent.mkdir(parents=True, exist_ok=True)
    # 排他创建；超限时只删除本次新建的包，原始实验不改动。
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr('bundle.json', json.dumps(dict(schema='akl.datacopy.bundle.v1', runs=index,
            omitted='可重建的图片/报告/events；失败输出数组留在原目录，失败状态和error.txt保留'), ensure_ascii=False))
        for path, name in files:
            archive.write(path, name)
    size = output.stat().st_size
    if size >= limit:
        output.unlink()
        raise ValueError(f'压缩后{size}字节，超过回传上限；请每个ZIP只放一个run，不删除样本')
    return size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(f'{args.output}: {bundle(args.runs, args.output)} bytes (< 5,000,000)')


if __name__ == '__main__':
    main()
