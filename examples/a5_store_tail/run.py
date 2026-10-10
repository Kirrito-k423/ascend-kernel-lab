"""纯写两窗口 / 仅末尾完成对照；同进程配对、逐启动 oracle、空闲门禁。"""
import argparse,csv,hashlib,json,random,re,shutil,subprocess
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,j):p.write_text(json.dumps(j,ensure_ascii=False,indent=2)+'\n')
def idle(folder,label):
    r=subprocess.run(['npu-smi','info'],capture_output=True,text=True,timeout=20)
    p=folder/f'occupancy-{label}.txt';p.write_text(r.stdout+r.stderr)
    if r.returncode or re.search(r'^\|\s*\d+\s*\|\s*\d+\s*\|',p.read_text(),re.M) or 'No running processes found' not in p.read_text():
        raise RuntimeError('NPU 占用快照忙或不完整，停止测量')
    return dict(observed_idle=True,sha256=sha(p))
def cases(max_cores,smoke=False):
    ns=sorted({n for n in [1,16,32,64,max_cores] if 0<n<=max_cores})
    if smoke:return [(900+i,1,0,n,t,b,1<<20,8<<20,0) for i,(n,t,b) in enumerate([(1,4096,1),(max_cores,4096,1),(1,32768,2),(max_cores,32768,2)])]
    presets=[(4096,1),(4096,8),(32768,2),(65536,1)]
    rows=[(i,1,0,n,t,b,1<<30,2<<30,0) for i,(n,t,b) in enumerate((n,t,b) for n in ns for t,b in presets)]
    rows += [(len(rows)+i,1,0,max_cores,t,b,2<<30,4<<30,0) for i,(t,b) in enumerate(presets)]
    return rows

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',type=int,default=1);p.add_argument('--round',type=int,choices=[1,2],required=True)
    p.add_argument('--case-id',type=int,nargs='+');p.add_argument('--smoke',action='store_true')
    p.add_argument('--warmup',type=int,default=2);p.add_argument('--samples',type=int,default=12)
    p.add_argument('--plan-only',action='store_true');p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    env=json.loads((ROOT/'results/setup/environment.json').read_text());profile=json.loads((ROOT/'results/setup/profile.json').read_text())
    canonical=cases(env['aiv_count'],a.smoke);order=canonical.copy();seed=202610100+a.round
    random.Random(seed).shuffle(order)
    selected=[r for r in order if a.case_id is None or r[0] in a.case_id]
    if not selected or (a.case_id is not None and set(a.case_id)!=set(r[0] for r in selected)):raise ValueError('配置不存在')
    with (a.output/'plan.csv').open('w') as f:csv.writer(f).writerows(selected)
    if a.plan_only:
        save(a.output/'plan.json',dict(schema='akl.store-tail.plan.v1',round=a.round,seed=seed,canonical=canonical,order=order));return
    binary=ROOT/'build-store-tail/akl_store_tail';build=json.loads(binary.with_suffix('.build.json').read_text())
    if sha(binary)!=build['executable_sha256']:raise ValueError('二进制凭据错配')
    for path,h in build['source_sha256'].items():
        if sha(ROOT/path)!=h:raise ValueError('源码变化，需要重新构建')
    m=dict(schema='akl.store-tail.mbench.v1',status='incomplete',round=a.round,device=a.device,smoke=a.smoke,
           environment=env,clock_profile=profile,build=build,plan_sha256=sha(a.output/'plan.csv'),
           order_seed=seed,warmup=a.warmup,samples=a.samples,started_utc=datetime.now(timezone.utc).isoformat())
    save(a.output/'manifest.json',m)
    try:
        m['occupancy_before']=idle(a.output,'before');save(a.output/'manifest.json',m)
        with (a.output/'stdout.log').open('w') as stdout,(a.output/'stderr.log').open('w') as stderr:
            subprocess.run([str(binary),str(a.device),str(a.output/'plan.csv'),str(a.warmup),str(a.samples),
                            str(a.output/'samples.jsonl'),str(env['ub_bytes']),str(seed)],check=True,timeout=240,stdout=stdout,stderr=stderr)
        m['occupancy_after']=idle(a.output,'after')
        m.update(status='validated',samples_sha256=sha(a.output/'samples.jsonl'))
    except BaseException as e:m.update(status='failed',error=str(e));raise
    finally:m['ended_utc']=datetime.now(timezone.utc).isoformat();save(a.output/'manifest.json',m)
if __name__=='__main__':main()
