#!/usr/bin/env python3
"""单机两设备或双节点两进程；不自动推断机柜/不合并跨节点绝对时钟。"""
import argparse,hashlib,json,os,re,signal,subprocess,time
from pathlib import Path

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,o):p.write_text(json.dumps(o,ensure_ascii=False,indent=2))
def snapshot(out,label):
    r=subprocess.run(['npu-smi','info'],capture_output=True,text=True,timeout=30,check=True)
    (out/f'occupancy-{label}.txt').write_text(r.stdout+r.stderr)
    process_table=r.stdout[r.stdout.find('Process id'):] if 'Process id' in r.stdout else r.stdout
    if 'No running processes found' not in process_table or re.search(r'^\|\s*\d+\s*\|\s*\d+\s*\|',process_table,re.M):
        raise RuntimeError('发现其他 NPU 进程；本次不启动/不验收，不中止其他任务')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--binary',type=Path,required=True);p.add_argument('--library-dir',type=Path,required=True)
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--engine',choices=['mte','urma'],required=True)
    p.add_argument('--bootstrap',default='tcp://127.0.0.1:29850')
    p.add_argument('--devices',type=int,nargs=2,default=[0,1]);p.add_argument('--rank',type=int,choices=[0,1])
    p.add_argument('--session',required=True);p.add_argument('--warmup',type=int,default=2)
    p.add_argument('--samples',type=int,default=6);p.add_argument('--timeout',type=int,default=600)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--placement',choices=['same_host','same_cabinet','cross_cabinet','unknown'],default='unknown')
    p.add_argument('--topology-evidence',type=Path)
    a=p.parse_args()
    if a.placement in ['same_cabinet','cross_cabinet'] and not a.topology_evidence:
        p.error('柜号必须有 topology-evidence；仅机器编号不够')
    if a.samples<1 or a.warmup<0 or a.timeout<1:p.error('样本数/超时非法')
    a.output.mkdir(parents=True,exist_ok=False)
    a.binary=a.binary.resolve();a.plan=a.plan.resolve();a.library_dir=a.library_dir.resolve()
    if not (a.library_dir/'libshmem.so').is_file():raise ValueError('library-dir 缺少 libshmem.so')
    # 可执行文件的 RUNPATH 不传递给间接依赖；显式选择完整的同版本 SHMEM 库目录。
    env=dict(os.environ,SHMEM_UID_SESSION_ID=a.session,
        LD_LIBRARY_PATH=str(a.library_dir)+':'+os.environ.get('LD_LIBRARY_PATH',''))
    libraries={f.name:sha(f) for f in sorted(a.library_dir.glob('*.so*')) if f.is_file()}
    ranks=[0,1] if a.rank is None else [a.rank];procs=[];files=[]
    manifest=dict(schema='akl.network.run.v1',status='incomplete',session=a.session,
        placement=a.placement,engine=a.engine,ranks=ranks,devices=a.devices,warmup=a.warmup,samples=a.samples,
        binary_sha256=sha(a.binary),plan_sha256=sha(a.plan),shmem_library_sha256=libraries,started=time.time(),
        topology_evidence_sha256=sha(a.topology_evidence) if a.topology_evidence else None,
        timing='initiator ACL Event through completion; host coordination outside event',
        fabric_exclusive=False)
    save(a.output/'manifest.json',manifest)
    try:
        snapshot(a.output,'before');(a.output/'plan.csv').write_bytes(a.plan.read_bytes())
        if a.topology_evidence:(a.output/'topology-evidence.json').write_bytes(a.topology_evidence.read_bytes())
        for rank in ranks:
            log=(a.output/f'rank{rank}.stdout').open('w');err=(a.output/f'rank{rank}.stderr').open('w');files.extend([log,err])
            cmd=[str(a.binary),str(a.devices[rank]),str(rank),a.bootstrap,a.engine,str(a.plan),
                 str(a.warmup),str(a.samples),str(a.output/f'rank{rank}.jsonl')]
            procs.append(subprocess.Popen(cmd,env=env,stdout=log,stderr=err,start_new_session=True))
        deadline=time.monotonic()+a.timeout
        while any(proc.poll() is None for proc in procs):
            if any(proc.poll() not in [None,0] for proc in procs):raise RuntimeError('rank 失败；保留 stderr 和原始样本')
            if time.monotonic()>deadline:raise TimeoutError('仅终止本 runner 启动的进程组')
            time.sleep(.2)
        if any(proc.returncode for proc in procs):raise RuntimeError('rank 退出码非零')
        snapshot(a.output,'after')
        count=len([line for line in a.plan.read_text().splitlines() if line and not line.startswith('#')])
        for rank in ranks:
            raw=a.output/f'rank{rank}.jsonl';rows=[json.loads(line) for line in raw.read_text().splitlines()]
            if len(rows)!=count*(a.warmup+a.samples) or any(r['mismatches'] or r['rank']!=rank for r in rows):
                raise RuntimeError('输出数量或 oracle 错误')
            if rank==0 and any(r['event_ms']<=0 or len(r['ticks'])!=r['cores']*4 for r in rows):
                raise RuntimeError('原始计时缺失')
        manifest.update(status='validated' if len(ranks)==2 else 'local_rank_validated',
            output_sha256={f'rank{rank}.jsonl':sha(a.output/f'rank{rank}.jsonl') for rank in ranks})
    except BaseException as e:
        manifest.update(status='failed',error=str(e));raise
    finally:
        for proc in procs:
            if proc.poll() is None:os.killpg(proc.pid,signal.SIGTERM)
        for proc in procs:
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
        for f in files:f.close()
        manifest['ended']=time.time();save(a.output/'manifest.json',manifest)
if __name__=='__main__':main()
