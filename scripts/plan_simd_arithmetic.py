#!/usr/bin/env python3
"""同工作量的 Tensor SIMD / REG SIMD / SIMT；尾块与空计算独立标记。"""
import argparse, csv, json, random
from pathlib import Path

SHAPES = [1,31,32,33,63,64,65,128,256,512,1024,2048,4096,8192,16384]
MODES = [(0,32),(2,32),(3,32)] + [(1,t) for t in [32,128,512,1024,2048]]

def cases():
    rows=[]
    for n in SHAPES:
        for steps in [1,16,128]:
            for op in range(4):
                for impl,threads in MODES: rows.append((impl,op,n,threads,steps,1))
    for n in [2048,8192]:
        for op in range(4):
            for impl,threads in MODES: rows.append((impl,op,n,threads,512,1))
    for n in [32,64,128,256,512,1024,2048,4096,8192,16384]:
        for impl,threads in MODES: rows.append((impl,0,n,threads,0,1))
    for op in range(4): rows.append((1,op,129,1,1,1))
    return [(i,*r) for i,r in enumerate(rows)]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--chunk-size',type=int,default=16)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    canonical=cases()
    for round_ in [1,2]:
        folder=a.output/f'round-{round_}';folder.mkdir()
        rows=canonical.copy();random.Random(20261009+round_).shuffle(rows)
        for index,start in enumerate(range(0,len(rows),a.chunk_size)):
            with (folder/f'chunk-{index:03}.csv').open('w') as f:
                csv.writer(f).writerows(rows[start:start+a.chunk_size])
    (a.output/'plan.json').write_text(json.dumps(dict(schema='akl.simd.plan.v1',
        configs=len(canonical),rounds=2,shapes=SHAPES,steps=[1,16,128,512],
        modes=MODES,seed=20261009,chunk_size=a.chunk_size),indent=2))
    print(len(canonical),'configs; 两轮独立随机顺序')

if __name__=='__main__':main()
