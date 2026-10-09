#!/usr/bin/env python3
"""单卡多 AIV 的共同区间吞吐；同一曲线各核数复用同一次 GM 分配。"""
import argparse,csv,json,random
from pathlib import Path

PRESETS=[(4096,1),(4096,8),(32768,2),(65536,1)]
def curves(max_cores):
    ns=sorted({n for n in [1,2,4,8,12,16,20,24,28,32,40,48,56,64,max_cores] if n<=max_cores})
    result=[];index=0
    for direction in [0,1]:
        for ring in [4<<20,1<<30]:
            for tile,batch in PRESETS:
                curve=[]
                for n in ns:
                    curve.append((index,direction,0,n,tile,batch,ring,2<<30,0));index+=1
                result.append(curve)
    curve=[]
    for n in ns:curve.append((index,0,1,n,32768,2,128<<10,2<<30,0));index+=1
    result.append(curve)
    for direction in [0,1]:
        curve=[]
        for n in ns:curve.append((index,direction,0,n,32768,2,4<<20,2<<30,1));index+=1
        result.append(curve)
    # 后续阶段：2GiB 地址覆盖，检查 1GiB 的观察是否依赖工作集/分区跨度。
    # 追加 ID，保留首阶段的全部配置编号和原始结果。
    for direction in [0,1]:
        for tile,batch in PRESETS:
            curve=[]
            for n in ns:
                curve.append((index,direction,0,n,tile,batch,2<<30,4<<30,0));index+=1
            result.append(curve)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--max-cores',type=int,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.max_cores<1:raise ValueError('可用核数无效')
    a.output.mkdir(parents=True,exist_ok=False)
    canonical=curves(a.max_cores)
    for round_ in [1,2]:
        folder=a.output/f'round-{round_}';folder.mkdir()
        # 保留实际测量的两阶段顺序；扩展工作集时不重排或覆盖首阶段。
        index=0
        for phase,seed in [(canonical[:19],20261009),(canonical[19:],20261010)]:
            rs=phase.copy();rng=random.Random(seed+round_);rng.shuffle(rs)
            for curve in rs:
                rows=curve.copy();rng.shuffle(rows)
                with (folder/f'chunk-{index:03}.csv').open('w') as f:csv.writer(f).writerows(rows)
                index+=1
    (a.output/'plan.json').write_text(json.dumps(dict(schema='akl.bandwidth.plan.v1',max_cores=a.max_cores,
        configs=sum(map(len,canonical)),curves=len(canonical),rounds=2,seeds=[20261009,20261010],
        saturation='N95: 最小核数达到该曲线两轮中位数观测最大值的 95%；相邻后续点另验平台',
        ring_requested=[4<<20,1<<30,2<<30],target_bytes=[2<<30,4<<30]),ensure_ascii=False,indent=2))
    print(sum(map(len,canonical)),'configs;',len(canonical),'曲线 / 轮')
if __name__=='__main__':main()
