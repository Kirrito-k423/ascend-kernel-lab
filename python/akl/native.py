"""ACL资源管理；只管理本进程分配的资源。"""
import ctypes as C
import os, platform, configparser
from pathlib import Path

class Runtime:
    def __init__(self, library, device):
        self.device, self.buffers = device, []
        self.acl = C.CDLL("libascendcl.so")
        v, i, u, z = C.c_void_p, C.c_int, C.c_uint32, C.c_size_t
        specs = {
            "aclInit": [C.c_char_p], "aclFinalize": [],
            "aclrtSetDevice": [i], "aclrtResetDevice": [i],
            "aclrtCreateStream": [C.POINTER(v)], "aclrtDestroyStream": [v],
            "aclrtSynchronizeStream": [v],
            "aclrtMalloc": [C.POINTER(v), z, i], "aclrtFree": [v],
            "aclrtMemcpy": [v,z,v,z,i],
            "aclrtGetDeviceInfo": [u,i,C.POINTER(C.c_int64)],
        }
        for name,args in specs.items():
            f=getattr(self.acl,name); f.argtypes=args; f.restype=i
        self.acl.aclrtGetSocName.restype=C.c_char_p
        self.lib=C.CDLL(str(library))
        self.lib.akl_params_size.restype=u
        if self.lib.akl_params_size()!=60: raise RuntimeError("设备参数ABI大小不匹配")
        self.lib.akl_launch.argtypes=[u,v,v,v,v,v,v,i]
        self.lib.akl_launch.restype=None
        self.stream=v()
        self.check("aclInit", None)
        try:
            self.check("aclrtSetDevice",device)
            self.check("aclrtCreateStream",C.byref(self.stream))
        except BaseException:
            self.acl.aclFinalize()
            raise

    def check(self, name, *args):
        code=getattr(self.acl,name)(*args)
        if code: raise RuntimeError(f"{name} 失败，ACL错误码={code}")

    def info(self):
        info={"soc": self.acl.aclrtGetSocName().decode()}
        for label,attr in (("aiv_count",201),("ub_bytes",204)):
            val=C.c_int64()
            self.check("aclrtGetDeviceInfo",self.device,attr,C.byref(val))
            info[label]=val.value
        if info['ub_bytes'] <= 0:
            path=Path(os.environ['ASCEND_HOME_PATH'])/(platform.machine()+'-linux')/'data/platform_config'/(info['soc']+'.ini')
            config=configparser.ConfigParser(strict=False)
            config.read(path)
            values=[int(config[s]['ub_size']) for s in config.sections() if 'ub_size' in config[s]]
            if not values or min(values)<=0: raise RuntimeError('运行时UB容量未知且平台配置不可用')
            info['ub_bytes']=min(values)
            info['ub_source']=str(path)
            info['ub_note']='aclrtGetDeviceInfo返回0，使用同版本SoC平台配置'
        return info

    def alloc(self,n):
        p=C.c_void_p(); self.check("aclrtMalloc",C.byref(p),n,2)
        self.buffers.append(p)
        return p

    def upload(self,p,array):
        self.check("aclrtMemcpy",p,array.nbytes,C.c_void_p(array.ctypes.data),array.nbytes,1)

    def download(self,p,array):
        self.check("aclrtMemcpy",C.c_void_p(array.ctypes.data),array.nbytes,p,array.nbytes,2)

    def launch(self,cores,pointers,trace):
        self.lib.akl_launch(cores,self.stream,*pointers,int(trace))
        self.check("aclrtSynchronizeStream",self.stream)

    def free_buffers(self):
        # 同步由调用方保证；发生kernel错误时由进程清理ACL资源。
        for p in reversed(self.buffers): self.check("aclrtFree",p)
        self.buffers.clear()

    def close(self):
        errors=[]
        for p in reversed(self.buffers):
            if self.acl.aclrtFree(p): errors.append("释放buffer失败")
        self.buffers.clear()
        if self.stream and self.acl.aclrtDestroyStream(self.stream): errors.append("释放stream失败")
        if self.acl.aclrtResetDevice(self.device): errors.append("释放设备上下文失败")
        if self.acl.aclFinalize(): errors.append("ACL结束失败")
        if errors: raise RuntimeError("；".join(errors))
