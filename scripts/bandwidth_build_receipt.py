#!/usr/bin/env python3
"""将 多 AIV DataCopy 可执行文件绑定到实际编译源码与工具链。"""
import hashlib,json,subprocess,sys
from pathlib import Path
root,executable,cann=map(Path,sys.argv[1:])
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
files=['examples/a5_bandwidth/kernel.cpp','examples/a5_bandwidth/main.cpp','examples/a5_bandwidth/CMakeLists.txt']
receipt=dict(schema='akl.bandwidth.build.v1',executable_sha256=sha(executable),npu_arch='dav-3510',
    source_sha256={n:sha(root/n) for n in files},
    compiler=subprocess.check_output([str(cann/'bin/bisheng'),'--version'],text=True),
    cann_install=next(cann.glob('*-linux/ascend_toolkit_install.info')).read_text())
executable.with_suffix('.build.json').write_text(json.dumps(receipt,indent=2))
