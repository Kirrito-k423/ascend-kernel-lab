#!/usr/bin/env python3
"""空闲前后快照 + 完整 oracle/保护区 + 构建绑定；短批次共享 A5 测量。"""
import argparse,hashlib,json,shutil,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
from run_borrowed_batch import idle

ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,obj):p.write_text(json.dumps(obj,ensure_ascii=False,indent=2))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',type=int,default=1)
    p.add_argument('--round',type=int,choices=[1,2],required=True)
    p.add_argument('--start',type=int,required=True)
    p.add_argument('--count',type=int,default=4)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    binary=ROOT/'build-simd/akl_simd_arithmetic'
    build=json.loads(binary.with_suffix('.build.json').read_text())
    if build['executable_sha256']!=sha(binary):raise ValueError('构建二进制发生变化')
    for name,hash_ in build['source_sha256'].items():
        if sha(ROOT/name)!=hash_:raise ValueError('源码与构建不一致')
    paths=sorted((ROOT/'results/simd-plan'/f'round-{a.round}').glob('chunk-*.csv'))
    selected=paths[a.start:a.start+a.count]
    if not selected:raise ValueError('批次不存在')
    batch=dict(schema='akl.simd.batch.v1',round=a.round,status='incomplete',chunks=[])
    save(a.output/'batch.json',batch)
    try:
        for index,path in enumerate(selected,a.start):
            out=a.output/f'simd-r{a.round}-c{index:03}';out.mkdir()
            shutil.copyfile(path,out/'plan.csv')
            meta=dict(schema='akl.simd.mbench.v1',status='incomplete',round=a.round,device=a.device,
                build=build,plan_sha256=sha(out/'plan.csv'),warmup=2,samples=15,
                started_utc=datetime.now(timezone.utc).isoformat(),
                clock_profile=json.loads((ROOT/'results/setup/profile.json').read_text()))
            save(out/'manifest.json',meta)
            meta['occupancy_before']=idle(out,'before')
            with (out/'stdout.log').open('w') as stdout,(out/'stderr.log').open('w') as stderr:
                subprocess.run([str(binary),str(a.device),str(path),'2','15',str(out/'samples.jsonl')],
                    check=True,timeout=90,stdout=stdout,stderr=stderr)
            meta['occupancy_after']=idle(out,'after')
            meta.update(status='validated',samples_sha256=sha(out/'samples.jsonl'),
                ended_utc=datetime.now(timezone.utc).isoformat())
            save(out/'manifest.json',meta)
            batch['chunks'].append(dict(index=index,run=out.name,status='validated'))
            save(a.output/'batch.json',batch)
            print('VALIDATED',a.round,index,flush=True)
        batch['status']='validated'
    except BaseException as error:
        batch.update(status='interrupted',error=str(error));raise
    finally:save(a.output/'batch.json',batch)

if __name__=='__main__':main()
