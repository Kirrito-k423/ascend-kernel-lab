#!/usr/bin/env python3
"""运行单 AIV DataCopy 基线；完整保存原始计时与正确性，不覆盖旧实验。"""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tarfile
import time
import traceback
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
import numpy as np
from akl.datacopy import CopyCase, SCHEMA, make_buffers, suite
from akl.native import Runtime
from akl.trace import decode, duration_ticks
from akl.cases import WORDS


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path, obj): path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False))


def occupancy(out, label):
    run = subprocess.run(['npu-smi', 'info'], capture_output=True, text=True, timeout=20)
    text = run.stdout + run.stderr
    (out / f'occupancy-{label}.txt').write_text(text)
    # 保守要求整台机器没有其他 NPU 进程，权限不足/格式变化均不能通过。
    idle = (run.returncode == 0 and 'No running processes found' in text
            and not re.search(r'^\|\s*\d+\s+\d+\s+\d+\s+', text, re.M))
    if not idle:
        raise RuntimeError(f'npu-smi 未证实设备空闲，见 occupancy-{label}.txt；未终止任何其他任务')
    return dict(sha256=sha(out / f'occupancy-{label}.txt'), observed_idle=True)


def execute(args):
    root = Path(__file__).resolve().parents[1]
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    manifest = dict(schema=SCHEMA, status='incomplete', run_id=out.name,
                    started_utc=datetime.now(timezone.utc).isoformat(), cases=[], seed=args.seed,
                    arguments=vars(args), device=args.device)
    rt = None
    save(out / 'manifest.json', manifest)
    try:
        profile = json.loads(Path(args.profile).read_text())
        if profile.get('schema') != 'akl.datacopy.profile.v1': raise ValueError('未知环境 profile')
        for key in ('soc', 'npu_arch', 'topology', 'clock_source'):
            if not isinstance(profile.get(key), str) or not profile[key].strip(): raise ValueError(f'缺少 profile {key}')
        if profile.get('memory_scope') != 'local_GM': raise ValueError('本 kernel 只支持本地 GM')
        if type(profile.get('clock_hz')) is not int or profile['clock_hz'] <= 0: raise ValueError('必须指定有依据的时钟频率')
        manifest['profile'] = profile
        library = Path(args.library).resolve()
        build = json.loads(library.with_suffix('.build.json').read_text())
        if build.get('schema') != 'akl.datacopy.build.v1' or build['library_sha256'] != sha(library):
            raise ValueError('库与构建凭据不符')
        if build['npu_arch'] != profile['npu_arch']: raise ValueError('profile 与构建架构不符')
        for name, value in build['source_sha256'].items():
            if sha(root / name) != value: raise ValueError(f'二进制源码已过期：{name}；请重新编译')
        cann = Path(os.environ['ASCEND_HOME_PATH'])
        install = next(cann.glob('*-linux/ascend_toolkit_install.info')).read_text()
        if install != build['cann_install']: raise ValueError('运行 CANN 与构建 CANN 不符')
        manifest['build'] = build
        manifest['source_sha256'] = {str(p.relative_to(root)):sha(p) for folder in ('python', 'include', 'kernels', 'scripts', 'examples/datacopy')
                                    for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and not p.name.startswith('._')}
        with tarfile.open(out/'source.tar.gz', 'w:gz') as archive:
            for name in manifest['source_sha256']: archive.add(root/name, arcname=name)
            archive.add(root/'CMakeLists.txt', arcname='CMakeLists.txt')
        cases = [CopyCase(**x) for x in json.loads(Path(args.cases).read_text())] if args.cases else suite()
        if not cases or len({c.name for c in cases}) != len(cases): raise ValueError('case 为空或重名')
        for case in cases: case.params()
        random.Random(args.seed).shuffle(cases)
        manifest['occupancy_before'] = occupancy(out, 'before')
        rt = Runtime(library, args.device, symbol='akl_copy', params_size=64)
        hardware = rt.info()
        manifest['hardware'] = hardware
        if hardware['soc'] != profile['soc']: raise ValueError('profile 与运行芯片不符')
        for case in cases:
            p = case.params()
            if p['ub_stride_bytes'] * case.batch + WORDS*8 > hardware['ub_bytes']: raise ValueError('实际 UB 不足')
            folder = out/case.name
            folder.mkdir()
            record = dict(case=asdict(case), params=p, layout=case.layout(), status='incomplete')
            manifest['cases'].append(record)
            save(out/'manifest.json', manifest)
            x, y, expected, defined = make_buffers(case)
            dummy = np.zeros(8, dtype=np.uint32)
            raw = np.zeros((1, WORDS), dtype=np.uint64)
            config = np.frombuffer(case.pack(), dtype=np.uint32).copy()
            arrays = (x, dummy, y, raw, config)
            buffers = [rt.alloc(a.nbytes) for a in arrays]
            for ptr, array in zip(buffers, arrays): rt.upload(ptr, array)
            samples = []
            launches = [(True, True)] * args.warmup
            for i in range(args.samples):
                pair = [(True, False), (False, False)]
                random.Random(args.seed+i).shuffle(pair)
                launches.extend(pair)
            retained = case.block_bytes * case.blocks * case.batch if case.control == 'payload' else 0
            with (folder/'events.jsonl').open('w') as events_file:
                for launch, (trace, warmup) in enumerate(launches):
                    y.fill(0xa5)
                    raw.fill(0)
                    rt.upload(buffers[2], y)
                    rt.upload(buffers[3], raw)
                    start = time.perf_counter_ns()
                    rt.launch(1, buffers, trace)
                    host_us = (time.perf_counter_ns()-start)/1000
                    rt.download(buffers[2], y)
                    if not np.array_equal(y[defined], expected[defined]):
                        np.savez(folder/f'failed-output-{launch}.npz', actual=y, expected=expected, defined=defined)
                        raise AssertionError(f'{case.name} 输出/gap/guard 校验失败，launch={launch}')
                    sample = dict(launch=launch, trace=trace, warmup=warmup, correctness=True,
                                  host_launch_sync_us=host_us, output_sha256=hashlib.sha256(y.tobytes()).hexdigest())
                    if trace:
                        rt.download(buffers[3], raw)
                        np.save(folder/f'trace-{launch}.npy', raw)
                        events = decode(raw, out.name, launch, args.device, [retained])
                        sample['ticks'] = str(duration_ticks(events, 0))
                        for event in events: events_file.write(json.dumps(event)+'\n')
                    samples.append(sample)
                    save(folder/'samples.json', samples)
            record.update(status='validated', launches=len(samples), expected_retained=retained)
            rt.free_buffers()
            save(out/'manifest.json', manifest)
            print(f'通过 {case.name}: {len(samples)} launches', flush=True)
        rt.close()
        rt = None
        manifest['occupancy_after'] = occupancy(out, 'after')
        manifest['isolation'] = 'single_AIV; exclusive local buffers; npu-smi idle before/after; transient interference not excluded'
        manifest['status'] = 'validated'
    except BaseException as error:
        manifest.update(status='failed', error=str(error))
        (out/'error.txt').write_text(traceback.format_exc())
        raise
    finally:
        try:
            if rt: rt.close()
        except Exception as error:
            manifest.update(status='failed', cleanup_error=str(error))
            raise
        finally:
            manifest['ended_utc'] = datetime.now(timezone.utc).isoformat()
            save(out/'manifest.json', manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--cases', help='CopyCase JSON 列表；省略使用基本矩阵')
    parser.add_argument('--device', type=int, required=True)
    parser.add_argument('--library', default='build/libakl_datacopy.so')
    parser.add_argument('--warmup', type=int, default=3)
    parser.add_argument('--samples', type=int, default=20)
    parser.add_argument('--seed', type=int, default=20260923)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.warmup < 0 or args.samples < 1 or args.device < 0: parser.error('采样/设备参数非法')
    execute(args)

if __name__ == '__main__': main()
