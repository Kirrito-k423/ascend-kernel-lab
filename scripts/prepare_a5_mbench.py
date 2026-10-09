#!/usr/bin/env python3
"""从实际 Runtime 保存设备属性，配置显式时钟依据，并生成边界冒烟。"""
from dataclasses import asdict
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
from akl.native import Runtime
from akl.datacopy import CopyCase

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--device',type=int,default=0)
p.add_argument('--clock-hz',type=int,required=True)
p.add_argument('--clock-source',required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
if a.clock_hz<=0: p.error('必须提供有效且有依据的时钟频率')
a.output.mkdir(parents=True,exist_ok=True)
rt=Runtime(Path('build-a5/libakl_datacopy.so').resolve(),a.device,symbol='akl_copy',params_size=64)
info=rt.info();rt.close()
if '950' not in info['soc']: raise ValueError('此轮只在 A5 执行')
print(info)
(a.output/'hardware.json').write_text(json.dumps(info,indent=2))
profile=dict(schema='akl.datacopy.profile.v1',soc=info['soc'],npu_arch='dav-3510',
             topology='A5 borrowed node',memory_scope='local_GM',clock_hz=a.clock_hz,clock_source=a.clock_source)
(a.output/'profile.json').write_text(json.dumps(profile,indent=2))
cases=[]
for direction in ('GM_UB','UB_GM'):
    for dtype,n,offset in (('uint8',129,1),('float32',516,4),('uint8',128,0)):
        cases.append(asdict(CopyCase(f'smoke_{direction}_{dtype}_{n}',direction=direction,
            api='DataCopyPad_params',dtype=dtype,block_bytes=n,gm_offset_bytes=offset,loops=64)))
(a.output/'smoke-cases.json').write_text(json.dumps(cases,indent=2))
