#!/usr/bin/env python3
"""构建结束后绑定源码、CANN 与动态库；供上板运行拒绝陈旧二进制。"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
root, library, arch, cann = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
files = [root/'kernels/datacopy.cpp', root/'CMakeLists.txt', root/'scripts/copy_build_receipt.py']
files += list((root/'include/akl').rglob('*.h'))
compiler = subprocess.check_output([str(cann/'bin/bisheng'), '--version'], text=True)
install = next(cann.glob('*-linux/ascend_toolkit_install.info'))
receipt = dict(schema='akl.datacopy.build.v1', library_sha256=sha(library), npu_arch=arch,
               compiler=compiler, cann_install=install.read_text(),
               source_sha256={str(p.relative_to(root)): sha(p) for p in files})
library.with_suffix('.build.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
