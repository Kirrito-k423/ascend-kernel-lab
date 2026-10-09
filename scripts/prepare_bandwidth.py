#!/usr/bin/env python3
"""查询当前 A5 的 AIV/UB 与同版本平台 L2，保存显式时钟依据。"""
import argparse,configparser,ctypes as C,hashlib,json,os,platform
from pathlib import Path
from run_borrowed_batch import idle

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',type=int,required=True)
    p.add_argument('--clock-hz',type=int,required=True)
    p.add_argument('--clock-source',required=True)
    p.add_argument('--output',type=Path,default=Path('results/setup'))
    a=p.parse_args()
    if a.device<0 or a.clock_hz<=0 or not a.clock_source.strip():p.error('设备与时钟依据无效')
    a.output.mkdir(parents=True,exist_ok=False);idle(a.output,'before')
    acl=C.CDLL('libascendcl.so')
    for name,args in {'aclInit':[C.c_char_p],'aclFinalize':[],
        'aclrtSetDevice':[C.c_int],'aclrtResetDevice':[C.c_int],
        'aclrtGetDeviceInfo':[C.c_uint32,C.c_int,C.POINTER(C.c_int64)]}.items():
        f=getattr(acl,name);f.argtypes=args;f.restype=C.c_int
    acl.aclrtGetSocName.argtypes=[];acl.aclrtGetSocName.restype=C.c_char_p
    def check(name,*args):
        rc=getattr(acl,name)(*args)
        if rc:raise RuntimeError(f'{name}: ACL={rc}')
    check('aclInit',None);selected=False
    try:
        check('aclrtSetDevice',a.device);selected=True
        env={'soc':acl.aclrtGetSocName().decode()}
        if '950' not in env['soc']:raise ValueError('此工程只支持 Ascend950/dav-3510')
        for label,attr in [('aiv_count',201),('ub_bytes',204)]:
            value=C.c_int64();check('aclrtGetDeviceInfo',a.device,attr,C.byref(value));env[label]=value.value
        cann=Path(os.environ['ASCEND_HOME_PATH'])
        path=cann/(platform.machine()+'-linux')/'data/platform_config'/(env['soc']+'.ini')
        cfg=configparser.ConfigParser(strict=False);cfg.read_string(path.read_text())
        def size(key):
            values={int(cfg[s][key]) for s in cfg.sections() if key in cfg[s]}
            if len(values)!=1 or min(values)<=0:raise ValueError('平台容量不确定：'+key)
            return values.pop()
        if env['aiv_count']<=0:raise ValueError('运行时 AIV 未知')
        if env['ub_bytes']<=0:
            env['ub_bytes']=size('ub_size');env['ub_source']='同版本 SoC 平台配置（ACL 返回 0）'
        env['l2_bytes']=size('l2_size')
        env['platform_config_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        install=next(cann.glob('*-linux/ascend_toolkit_install.info')).read_text()
        version=next(l.split('=',1)[1] for l in install.splitlines() if l.startswith('version='))
        env['l2_source']=f'CANN {version} {env["soc"]}.ini'
        profile=dict(schema='akl.datacopy.profile.v1',soc=env['soc'],npu_arch='dav-3510',
            topology='A5 borrowed node',memory_scope='local_GM',clock_hz=a.clock_hz,clock_source=a.clock_source)
    finally:
        try:
            if selected:check('aclrtResetDevice',a.device)
        finally:check('aclFinalize')
    idle(a.output,'after')
    for name,value in [('environment',env),('profile',profile)]:
        (a.output/f'{name}.json').write_text(json.dumps(value,ensure_ascii=False,indent=2))
    (a.output/'platform-config.ini').write_bytes(path.read_bytes())
    print(json.dumps(env,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
