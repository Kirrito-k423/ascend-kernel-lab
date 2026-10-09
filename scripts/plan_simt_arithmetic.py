#!/usr/bin/env python3
"""生成相同工作量对照与 VF 最小调用体的独立短批次。"""
import argparse
from pathlib import Path
import random


def plan():
    result=[]
    threads=(1,8,16,32,64,128,256,512,1024,2048)
    for op in range(4):
        for n in (32,128,512,2048,8192):
            for steps in (1,16,128):
                result.extend((impl,op,n,32,steps,1) for impl in (0,2))
                result.extend((1,op,n,t,steps,1) for t in threads)
    # 线程体的写入数量与线程数一致，末尾补齐仅用于 DMA 导出。
    for t in threads:
        for calls in (1,16,64,128):
            result.append((3,0,max(32,t),t,1,calls))
    result.extend((4,0,32,32,1,c) for c in (1,16,64,128))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--chunk-size',type=int,default=16)
    a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    cases=[(i,*c) for i,c in enumerate(plan())]
    random.Random(20261009).shuffle(cases)
    def save(path, rows): path.write_text('\n'.join(','.join(map(str,r)) for r in rows)+'\n')
    save(a.output/'cases.csv',cases)
    for i in range(0,len(cases),a.chunk_size): save(a.output/f'chunk-{i//a.chunk_size:03}.csv',cases[i:i+a.chunk_size])
    # 冒烟覆盖每种实现与运算，以及 launch bound 的两个端点。
    smoke=[(i,*c) for i,c in enumerate(plan()) if c[2]==32 and c[4]==16 and c[3] in (1,32,2048)]
    smoke += [(9000,3,0,2048,2048,1,16),(9001,4,0,32,32,1,16)]
    save(a.output/'smoke.csv',smoke)
    print(f'{len(cases)} 个配置，{len(smoke)} 个冒烟配置')


if __name__=='__main__': main()
