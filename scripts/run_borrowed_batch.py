#!/usr/bin/env python3
"""仅执行已确认空闲时的短批次；保存每批占用证据，不重跑已有输出。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime,timezone

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'python'))


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(path,obj): path.write_text(json.dumps(obj,ensure_ascii=False,indent=2))
def idle(folder,label):
    result=subprocess.run(['npu-smi','info'],capture_output=True,text=True,timeout=20)
    text=result.stdout+result.stderr
    (folder/f'occupancy-{label}.txt').write_text(text)
    process_rows=re.findall(r'^\|\s*\d+\s*\|\s*\d+\s*\|',text,re.M)
    if result.returncode or process_rows or 'No running processes found' not in text:
        raise RuntimeError('发现其他 NPU 进程或占用信息不完整，停止后续测量')
    return dict(observed_idle=True,sha256=sha(folder/f'occupancy-{label}.txt'))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kind',choices=['alignment','simt'],required=True)
    p.add_argument('--round',type=int,choices=[1,2],required=True)
    p.add_argument('--start',type=int,required=True)
    p.add_argument('--count',type=int,default=4)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    paths=sorted((root/'results'/('alignment-plan' if a.kind=='alignment' else 'simt-plan')).glob('chunk-*'))
    selected=paths[a.start:a.start+a.count]
    if not selected: raise ValueError('没有对应批次')
    manifest=dict(schema='akl.borrowed.mbench.v1',kind=a.kind,round=a.round,status='incomplete',
                  started_utc=datetime.now(timezone.utc).isoformat(),chunks=[])
    save(a.output/'batch.json',manifest)
    try:
        for index,path in enumerate(selected,a.start):
            out=a.output/f'{a.kind}-r{a.round}-c{index:03}'
            if a.kind=='alignment':
                check=a.output/f'check-{index:03}';check.mkdir()
                idle(check,'before')
                command=[sys.executable,str(root/'scripts/run_datacopy.py'),'--profile',str(root/'results/setup/profile.json'),
                    '--device','0','--library',str(root/'build-a5/libakl_datacopy.so'),'--cases',str(path),
                    '--warmup','2','--samples','12','--seed',str(20261009+a.round),'--output',str(out)]
                with (check/'stdout.log').open('w') as stdout,(check/'stderr.log').open('w') as stderr:
                    subprocess.run(command,check=True,timeout=60,stdout=stdout,stderr=stderr)
                idle(check,'after')
            else:
                out.mkdir()
                meta=dict(schema='akl.simt.mbench.v1',status='incomplete',round=a.round,
                          clock_profile=json.loads((root/'results/setup/profile.json').read_text()),
                          executable_sha256=sha(root/'build-simt/akl_simt_arithmetic'),
                          source_sha256={n:sha(root/n) for n in ['examples/a5_mbench/simt_kernel.cpp','examples/a5_mbench/simt_main.cpp','examples/a5_mbench/CMakeLists.txt']},
                          cann_install=(root/'results/setup/cann-install.txt').read_text(),compiler=(root/'results/setup/compiler.txt').read_text())
                shutil.copyfile(path,out/'plan.csv')
                meta['plan_sha256']=sha(out/'plan.csv')
                save(out/'manifest.json',meta)
                meta['occupancy_before']=idle(out,'before')
                command=[str(root/'build-simt/akl_simt_arithmetic'),'0',str(path),'2','15',str(out/'samples.jsonl')]
                with (out/'stdout.log').open('w') as stdout,(out/'stderr.log').open('w') as stderr:
                    subprocess.run(command,check=True,timeout=60,stdout=stdout,stderr=stderr)
                meta['occupancy_after']=idle(out,'after')
                meta.update(status='validated',samples_sha256=sha(out/'samples.jsonl'))
                save(out/'manifest.json',meta)
            manifest['chunks'].append(dict(index=index,run=out.name,status='validated'))
            save(a.output/'batch.json',manifest)
            print(f'VALIDATED {a.kind} round={a.round} chunk={index}',flush=True)
        manifest['status']='validated'
    except BaseException as error:
        manifest.update(status='interrupted',error=str(error));raise
    finally:
        manifest['ended_utc']=datetime.now(timezone.utc).isoformat();save(a.output/'batch.json',manifest)


if __name__=='__main__': main()
