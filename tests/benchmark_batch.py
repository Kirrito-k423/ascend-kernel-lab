"""64 rank × 20 launch × 64 AIV × 16 events; CPU synthetic conversion benchmark."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time

BASE = Path(sys.argv[1]).resolve()
MAGIC = 0x414B4C5452433031

def key(name):
    value = 2166136261
    for byte in name.encode()+b'\0': value = ((value^byte)*16777619)&0xffffffff
    return value

def make(root):
    root.mkdir()
    for rank in range(64):
        for launch in range(20):
            folder = root/f'rank{rank}-pid{rank+1000}-launch{launch}';folder.mkdir()
            (folder/'capture.json').write_text(json.dumps(dict(schema='akl.semantic.v1', alignment='unverified', capacity=16, blocks=64, rank=rank, device=rank%8)))
            words = []
            for block in range(64):
                words += [MAGIC,1,16,0,block,0,0,1]
                for seq in range(16):
                    words += [key(['Init','LW:wait','LW:copy','done'][seq%4]),2**60+launch*100000+block*2+seq*(10+block%7)]
            (folder/'trace.bin').write_bytes(struct.pack('<'+str(len(words))+'Q',*words))

def digest(root, only_last=False):
    value = hashlib.sha256(); total = 0
    launches = [r['launch'] for r in json.loads((root/'result/summary.json').read_text())['launches']]
    if only_last: launches = [max(launches)]
    for launch in launches:
        group = root/'result/launches'/f'launch{launch}'
        report = json.loads((group/'summary.json').read_text()); assert len(report['runs'])==64
        assert not any(r['status']!='ok' for r in report['runs'])
        value.update(json.dumps(report['stats'],sort_keys=True).encode())
        trace = json.loads((group/'trace.json').read_text())['traceEvents']
        for event in trace:
            if event.get('cat')=='debugclock':
                value.update(json.dumps({k:event['args'][k] for k in ('event_id','block','subblock','sequence','occurrence','tick','end_tick','duration_cycle')},sort_keys=True).encode())
                total+=1
    assert total==64*len(launches)*64*16
    return value.hexdigest(),total

if __name__=='__main__':
    source = BASE/'kernel.cpp';source.write_text(''.join(f'DebugClock("{name}");' for name in ['Init','LW:wait','LW:copy','done']))
    results = []
    for name, package, options in [('old','old',[]),('final1','new',['--jobs','1']),('final64','new',['--jobs','64']),('last64','new',['--jobs','64','--last-launch'])]:
        root = BASE/("data-"+name);make(root)
        env = dict(os.environ, PYTHONPATH=str(BASE/package/'python'))
        start = time.perf_counter()
        with (BASE/(name+'.log')).open('w') as log:
            subprocess.run([sys.executable,'-m','akl.semantic',str(root),'--source',str(source),*options],env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
        elapsed = time.perf_counter()-start
        paths = [p for p in root.rglob('*') if p.is_file()]
        checksum,events = digest(root)
        result = dict(variant=name,seconds=elapsed,bytes=sum(p.stat().st_size for p in paths),files=len(paths),events=events,digest=checksum,
                      last_digest=digest(root,True)[0],zip_files=sum(p.suffix=='.zip' for p in paths),raw_files=sum(p.name=='trace.bin' for p in paths))
        results.append(result);print(json.dumps(result),flush=True)
        assert (result['last_digest'] if name=='last64' else checksum)==(results[0]['last_digest'] if name=='last64' else results[0]['digest'])
        (BASE/'metrics.json').write_text(json.dumps(results,indent=2))
        # Only generated benchmark data; logs and metrics are retained.
        shutil.rmtree(root)
