#!/usr/bin/env python3
"""控制 GM 分配变量的短边界复验；保留批次占用和正确性证据。"""
import argparse,json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from plan_alignment_paired import paired
from run_borrowed_batch import idle,save

p=argparse.ArgumentParser();p.add_argument('--device',type=int,required=True);p.add_argument('--round',type=int,choices=(1,2),required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
root=Path(__file__).resolve().parents[1]
a.output.mkdir(parents=True,exist_ok=False)
m=dict(schema='akl.borrowed.mbench.v1',kind='alignmentPaired',round=a.round,device=a.device,status='incomplete',chunks=[],started_utc=datetime.now(timezone.utc).isoformat())
save(a.output/'batch.json',m)
try:
    idle(a.output,'before')
    plan_path=a.output/'plan.json';save(plan_path,paired())
    out=a.output/f'alignment-paired-r{a.round}-c000'
    command=[sys.executable,str(root/'scripts/run_datacopy.py'),'--profile',str(root/'results/setup/profile.json'),
        '--device',str(a.device),'--library',str(root/'build-a5/libakl_datacopy.so'),'--cases',str(plan_path),
        '--reuse-buffers','--warmup','2','--samples','12','--seed',str(20261020+a.round),'--output',str(out)]
    subprocess.run(command,check=True,timeout=180)
    idle(a.output,'after')
    m['chunks']=[dict(index=0,run=out.name,status='validated')];m['status']='validated'
except BaseException as e:m.update(status='interrupted',error=str(e));raise
finally:
    m['ended_utc']=datetime.now(timezone.utc).isoformat();save(a.output/'batch.json',m)
