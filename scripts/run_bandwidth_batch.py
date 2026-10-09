#!/usr/bin/env python3
"""每条曲线前后核对占用；原始 ACL 共同区间和逐核 SYS_CNT 均归档。"""
import argparse,hashlib,json,shutil,subprocess
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
    p.add_argument('--count',type=int,default=2)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    binary=ROOT/'build-bandwidth/akl_bandwidth';build=json.loads(binary.with_suffix('.build.json').read_text())
    if sha(binary)!=build['executable_sha256']:raise ValueError('二进制构建绑定失败')
    for name,value in build['source_sha256'].items():
        if sha(ROOT/name)!=value:raise ValueError('源码已变化，请重编译')
    env=json.loads((ROOT/'results/setup/environment.json').read_text())
    profile=json.loads((ROOT/'results/setup/profile.json').read_text())
    paths=sorted((ROOT/'results/bandwidth-plan'/f'round-{a.round}').glob('chunk-*.csv'))[a.start:a.start+a.count]
    if not paths:raise ValueError('曲线不存在')
    batch=dict(schema='akl.bandwidth.batch.v1',round=a.round,status='incomplete',chunks=[])
    save(a.output/'batch.json',batch)
    try:
        for index,path in enumerate(paths,a.start):
            out=a.output/f'bandwidth-r{a.round}-c{index:03}';out.mkdir();shutil.copyfile(path,out/'plan.csv')
            m=dict(schema='akl.bandwidth.mbench.v1',round=a.round,status='incomplete',device=a.device,
                environment=env,clock_profile=profile,build=build,plan_sha256=sha(out/'plan.csv'),
                warmup=2,samples=12,started_utc=datetime.now(timezone.utc).isoformat())
            save(out/'manifest.json',m);m['occupancy_before']=idle(out,'before')
            with (out/'stdout.log').open('w') as stdout,(out/'stderr.log').open('w') as stderr:
                subprocess.run([str(binary),str(a.device),str(path),'2','12',str(out/'samples.jsonl'),str(env['ub_bytes'])],
                    check=True,timeout=240,stdout=stdout,stderr=stderr)
            m['occupancy_after']=idle(out,'after')
            m.update(status='validated',samples_sha256=sha(out/'samples.jsonl'),ended_utc=datetime.now(timezone.utc).isoformat())
            save(out/'manifest.json',m)
            batch['chunks'].append(dict(index=index,run=out.name,status='validated'));save(a.output/'batch.json',batch)
            print('VALIDATED',a.round,index,flush=True)
        batch['status']='validated'
    except BaseException as e:batch.update(status='interrupted',error=str(e));raise
    finally:save(a.output/'batch.json',batch)
if __name__=='__main__':main()
