#!/usr/bin/env python3
"""生成长度与 GM 起点对齐的独立实验轴；所有候选先经过 CopyCase 校验。"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from akl.datacopy import CopyCase


def plan():
    result = []
    edge = (127, 128, 129, 159, 160, 161, 191, 192, 193, 223, 224, 225, 255, 256, 257)
    for dtype, element in (('uint8', 1), ('float32', 4)):
        for direction in ('GM_UB', 'UB_GM'):
            for windows in (1, 2):
                # shape 是元素数，同时明确保存 payload 字节数。
                for n in range(127, 258):
                    c = CopyCase(f'{dtype}_{direction}_n{n}_o0_w{windows}', direction=direction,
                        api='DataCopyPad_params', dtype=dtype, block_bytes=n*element,
                        loops=8192, windows=windows, slots=windows)
                    c.params(); result.append(asdict(c))
                # 固定长度，单独观察地址起点；不混进长度曲线。
                for n in edge:
                    for offset in (element, 31*element, 127*element):
                        c = CopyCase(f'{dtype}_{direction}_n{n}_o{offset}_w{windows}', direction=direction,
                            api='DataCopyPad_params', dtype=dtype, block_bytes=n*element,
                            gm_offset_bytes=offset, loops=8192, windows=windows, slots=windows)
                        c.params(); result.append(asdict(c))
                # 同一对齐长度的重载对照，不把重载差异叫作对齐损失。
                for n in range(128, 257):
                    if n*element % 32: continue
                    c = CopyCase(f'{dtype}_{direction}_params_n{n}_w{windows}', direction=direction,
                        api='DataCopy_params', dtype=dtype, block_bytes=n*element,
                        loops=8192, windows=windows, slots=windows)
                    c.params(); result.append(asdict(c))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--chunk-size', type=int, default=32)
    a = p.parse_args()
    if a.chunk_size < 1: p.error('chunk-size 必须为正数')
    a.output.mkdir(parents=True, exist_ok=False)
    cases = plan()
    # 每块均含两种方向与同步模式，避免长时间按同一方向排序测量。
    import random
    random.Random(20261009).shuffle(cases)
    (a.output/'cases.json').write_text(json.dumps(cases, indent=2))
    for i in range(0, len(cases), a.chunk_size):
        (a.output/f'chunk-{i//a.chunk_size:03}.json').write_text(json.dumps(cases[i:i+a.chunk_size], indent=2))
    print(f'{len(cases)} 个配置，{(len(cases)+a.chunk_size-1)//a.chunk_size} 个短批次')


if __name__ == '__main__': main()
