"""构建凭据绑定被测程序、完整执行与计划输入；机器信息仅留私有结果。"""
import hashlib,json,subprocess,sys
from pathlib import Path
root,executable,cann=map(Path,sys.argv[1:])
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
files=['examples/a5_store_tail/'+n for n in ['kernel.cpp','main.cpp','CMakeLists.txt','run.py','prepare.py','receipt.py']]
receipt=dict(schema='akl.store-tail.build.v1',executable_sha256=sha(executable),npu_arch='dav-3510',
    source_sha256={n:sha(root/n) for n in files},
    compiler=subprocess.check_output([str(cann/'bin/bisheng'),'--version'],text=True),
    cann_install=next(cann.glob('*-linux/ascend_toolkit_install.info')).read_text())
executable.with_suffix('.build.json').write_text(json.dumps(receipt,indent=2))
