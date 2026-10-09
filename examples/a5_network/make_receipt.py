#!/usr/bin/env python3
"""构建时固定 binary、测量源文件、动态库和 SDK 的内容哈希。"""
import argparse,hashlib
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--binary',type=Path,required=True);p.add_argument('--lib',type=Path,required=True)
p.add_argument('--sdk',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();root=Path(__file__).resolve().parent
files={f'src/{name}':root/name for name in ['main.cpp','kernel.cpp','CMakeLists.txt','run_pair.py','plan.py','import_results.py','make_receipt.py']}
files['build/akl_network']=a.binary
for path in sorted(a.lib.parent.glob('*.so*')):
 if path.is_file():files['lib/'+path.name]=path
for part in ['include','src/device','src/device_simt','src/host_device']:
 for path in sorted((a.sdk/part).rglob('*')):
  if path.is_file():files['sdk/'+str(path.relative_to(a.sdk))]=path
lines=[hashlib.sha256(path.read_bytes()).hexdigest()+'  '+name for name,path in files.items()]
a.output.write_text('\n'.join(lines)+'\n')
