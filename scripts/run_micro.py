#!/usr/bin/env python3
"""上板执行参数矩阵，保留正确性、原始记录与全部样本。"""
from pathlib import Path
import argparse, dataclasses, hashlib, json, os, random, subprocess, sys, time, traceback, tarfile
from datetime import datetime, timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"python"))
import numpy as np
from akl.cases import Case, WORDS, suite, make_inputs
from akl.native import Runtime
from akl.trace import decode, duration_ticks

def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def capture_environment(root,out,manifest):
    manifest['command']=sys.argv
    manifest['python']=sys.version
    manifest['numpy']=np.__version__
    for name in ('CMakeLists.txt','requirements.txt'):
        if (root/name).is_file(): manifest['source_sha256'][name]=file_hash(root/name)
    for p in (root/'examples').glob('*.json'):
        manifest['source_sha256'][str(p.relative_to(root))]=file_hash(p)
    with tarfile.open(out/'source.tar.gz','w:gz') as archive:
        for name in manifest['source_sha256']: archive.add(root/name,arcname=name)
    manifest['source_archive_sha256']=file_hash(out/'source.tar.gz')
    commands={'npu-smi.txt':['npu-smi','info'],
              'compiler.txt':[str(Path(os.environ['ASCEND_HOME_PATH'])/'bin/bisheng'),'--version']}
    for name,command in commands.items():
        try:
            result=subprocess.run(command,capture_output=True,text=True,timeout=15)
            (out/name).write_text(result.stdout+result.stderr)
            manifest.setdefault('environment_capture',{})[name]={'exit_code':result.returncode,'sha256':file_hash(out/name)}
        except (OSError,subprocess.TimeoutExpired) as error:
            manifest.setdefault('environment_capture',{})[name]={'error':str(error)}

def execute(args):
    root=Path(__file__).resolve().parents[1]
    out=Path(args.output).resolve(); out.mkdir(parents=True,exist_ok=False)
    manifest={"schema_version":"akl.micro.v1","run_id":out.name,"status":"incomplete",
              "started_utc":datetime.now(timezone.utc).isoformat(),"device":args.device,
              "arguments":vars(args),"seed":20260915,"clock":{"backend":"GetSystemCycle/SYS_CNT",
              "frequency_hz":50000000,"frequency_source":"CANN GetSystemCycle A3 官方文档",
              "alignment":"unverified","note":"同设备原始SYS_CNT展示不构成跨核时钟校准"},
              "timing":"每次API+对应流水到S完成同步，循环批量计时；输出搬运和trace导出在区间外",
              "cache":"重复访问同一小工作集；未声称冷缓存","library_sha256":file_hash(Path(args.library)),
              "source_sha256":{str(p.relative_to(root)):file_hash(p) for folder in ("include","kernels","python","scripts") for p in (root/folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts and not p.name.startswith('._')},
              "cann_path":os.environ.get("ASCEND_HOME_PATH"),"cases":[]}
    (out/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    rt=None
    cleanup_error=None
    try:
        capture_environment(root,out,manifest)
        rt=Runtime(Path(args.library).resolve(),args.device)
        manifest["hardware"]=rt.info()
        if manifest["hardware"]["soc"]!="Ascend910_9382":
            raise RuntimeError("初版只验证Ascend910_9382；其他型号需先适配架构、容量与时钟频率")
        maxcores=min(args.max_cores,manifest["hardware"]["aiv_count"])
        cases=suite(maxcores,args.smoke)
        if args.case_json:
            cases=[Case(**x) for x in json.loads(Path(args.case_json).read_text())]
        if len({case.name for case in cases}) != len(cases): raise ValueError('实验名称重复，请为每个配置使用唯一名称')
        for case in cases: case.params()
        random.Random(20260915).shuffle(cases)
        for case in cases:
            p=case.params()
            if case.cores>manifest["hardware"]["aiv_count"]: raise ValueError("请求核数超过设备上限")
            if p["output_stride"]*8+4096+WORDS*8>manifest["hardware"]["ub_bytes"]:
                raise ValueError("UB需求超过运行时容量")
            record={"case":dataclasses.asdict(case),"params":p,"status":"incomplete"}
            manifest["cases"].append(record)
            case_dir=out/case.name; case_dir.mkdir()
            x,mask,expected,indices=make_inputs(case)
            y=np.full(case.cores*p["output_stride"]+32,0xdeadbeef,dtype=np.uint32)
            raw=np.zeros((case.cores,WORDS),dtype=np.uint64)
            conf=np.frombuffer(case.pack(),dtype=np.uint32).copy()
            buffers=[rt.alloc(a.nbytes) for a in (x,mask,y,raw,conf)]
            for ptr,arr in zip(buffers,(x,mask,y,raw,conf)): rt.upload(ptr,arr)
            np.savez(case_dir/"inputs.npz",x=x,mask=mask)
            samples=[]
            launches=[(True,True)]*args.warmup
            for i in range(args.samples):
                pair=[(True,False),(False,False)]
                random.Random(20260915+i).shuffle(pair)
                launches.extend(pair)
            with (case_dir/"events.jsonl").open("w") as event_file:
                for launch_id,(trace,warmup) in enumerate(launches):
                    y.fill(0xdeadbeef); raw.fill(0)
                    rt.upload(buffers[2],y)
                    if trace: rt.upload(buffers[3],raw)
                    start=time.perf_counter_ns()
                    rt.launch(case.cores,buffers,trace)
                    host_us=(time.perf_counter_ns()-start)/1000
                    rt.download(buffers[2],y)
                    np.save(case_dir/f"output_{launch_id}.npy",y)
                    for core,oracle in enumerate(expected):
                        actual=y[core*p["output_stride"]+indices[core]]
                        if not np.array_equal(actual,oracle):
                            mismatch=np.flatnonzero(actual!=oracle)[:8]
                            raise AssertionError(f"{case.name} AIV {core} 输出错误：{mismatch.tolist()}, 实际{actual[mismatch]}, 预期{oracle[mismatch]}")
                        if case.op==4:
                            untouched=np.ones(p["output_stride"],dtype=bool)
                            untouched[indices[core]]=False
                            if np.any(y[core*p["output_stride"]:(core+1)*p["output_stride"]][untouched]!=0xdeadbeef):
                                raise AssertionError("DataCopy跨步写覆盖了gap")
                    if np.any(y[-32:]!=0xdeadbeef): raise AssertionError("输出越界破坏保护区")
                    events=[]
                    if trace:
                        rt.download(buffers[3],raw)
                        np.save(case_dir/f"trace_{launch_id}.npy",raw)
                        events=decode(raw,out.name,launch_id,args.device,[len(a) for a in expected])
                        for event in events: event_file.write(json.dumps(event,ensure_ascii=False)+"\n")
                    samples.append(dict(launch_id=launch_id,trace=trace,warmup=warmup,correctness=True,
                        host_launch_sync_us=host_us,
                        per_core_ticks=[duration_ticks(events,c) for c in range(case.cores)] if trace else None))
                    (case_dir/"samples.json").write_text(json.dumps(samples,indent=2))
            (case_dir/"samples.json").write_text(json.dumps(samples,indent=2))
            record.update(status="validated",valid_launches=len(samples),expected_count=[len(e) for e in expected])
            rt.free_buffers()
            (out/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
            print(f"通过 {case.name}，{case.cores} AIV，{len(samples)}次启动",flush=True)
        manifest["status"]="validated"
    except BaseException as error:
        manifest["status"]="failed"
        manifest["error"]=str(error)
        (out/"error.txt").write_text(traceback.format_exc())
        raise
    finally:
        if rt:
            try: rt.close()
            except Exception as error:
                cleanup_error=error
                manifest['cleanup_error']=str(error)
                manifest['status']='failed'
        manifest["ended_utc"]=datetime.now(timezone.utc).isoformat()
        (out/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    if cleanup_error: raise cleanup_error

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library",default="build/libakl_kernels.so",help="已编译动态库")
    parser.add_argument("--device",type=int,default=0,help="逻辑设备号")
    parser.add_argument("--max-cores",type=int,default=8,help="矩阵最大AIV数")
    parser.add_argument("--warmup",type=int,default=3,help="预热启动次数")
    parser.add_argument("--samples",type=int,default=10,help="每配置计时与关闭采集的成对样本数")
    parser.add_argument("--output",required=True,help="新的结果目录")
    parser.add_argument("--smoke",action="store_true",help="只运行三个小用例")
    parser.add_argument("--case-json",help="自定义Case列表JSON文件")
    args=parser.parse_args()
    if args.samples<1 or args.warmup<0 or not 1<=args.max_cores<=128: parser.error("采样次数或核数非法")
    execute(args)
if __name__=="__main__": main()
