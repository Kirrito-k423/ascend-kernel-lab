#!/usr/bin/env python3
"""固定 GM 分配的边界复验，保持同一重载、单窗口和 0 起点偏移。"""
import argparse,json
from pathlib import Path
from plan_alignment import plan

def paired():
    edge=(127,128,129,159,160,161,191,192,193,223,224,225,255,256,257)
    return [c for c in plan() if c['api']=='DataCopyPad_params' and c['gm_offset_bytes']==0 and c['windows']==1 and
            c['block_bytes']//(1 if c['dtype']=='uint8' else 4) in edge]

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(paired(),indent=2));print(len(paired()),'个配置')
