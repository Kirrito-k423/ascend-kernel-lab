# 在 A5 PoD 上测试 DataCopy

本流程尚未在A5上执行。先做单卡、单AIV、本地GM基线，再讨论PoD内的远端访问或竞争；拓扑标签本身不会把实验变成跨卡通信。

## 1. 从真实算子采集环境和 shape

使用 [DeepEP #43](https://gitcode.com/ChenDonYY/ascend_deepep/merge_requests/43) 及其固定的 [AKL #33](https://github.com/Kirrito-k423/ascend-kernel-lab/pull/33) 依赖。在已准备CANN、SHMEM和原始运行配置的DeepEP工作区执行，构建时保留本机原有RDMA等参数：

```bash
source /usr/local/Ascend/cann/set_env.sh
git submodule update --init 3rdparty/ascend-kernel-lab
DATACOPY_SHAPES_ON=ON bash scripts/build.sh -soc_type Ascend950
export AKL_MACHINE_LABEL=A5-PoD
export DISPATCH_CLOCK_DIR="$PWD/results/copy-shapes"
export AKL_AUTO_PARSE=0 PROFILE=off
# 沿用原用例的节点、设备和shape配置，先查看执行范围
bash scripts/run_v2_elastic_dispatch_precision_multi_node.sh --dry-run
bash scripts/run_v2_elastic_dispatch_precision_multi_node.sh
```

不需要先知道h/k/dtype：每个实际执行rank会记录tokens/h/k、API真实模板dtype、字节数、stride和调用次数，以及SoC、可读取的CANN安装版本。保留自动解析关闭状态，避免关联的原始trace被提前清理；先短运行并检查原精度用例是否通过。

## 2. 解码一次 launch，生成 A5 profile

```bash
export DEEPEP_ROOT="$PWD"
export AKL_ROOT="$DEEPEP_ROOT/3rdparty/ascend-kernel-lab"
export CAPTURE=/path/to/rank-pid-launch-capture
export SHAPES="$DEEPEP_ROOT/results/decoded-copy-shapes"
PYTHONPATH="$AKL_ROOT/python" python3 -m akl.datacopy_shapes "$CAPTURE" \
  --source "$DEEPEP_ROOT/kernels/elastic_dispatch.cpp" --output "$SHAPES"
cd "$AKL_ROOT"
python3 -m pip install -r requirements.txt
python3 - <<'PY'
import json, os
from pathlib import Path
m=json.loads((Path(os.environ['CAPTURE'])/'datacopy-shapes.json').read_text())
print('SoC:',m['soc'],'CANN:',m['cann_version'],'h/k:',m['h'],m['k'])
if not m['soc'].startswith('Ascend950'):
    raise SystemExit('SoC不是Ascend950系列，停止套用此profile；先核对具体架构和时钟')
p=dict(schema='akl.datacopy.profile.v1',soc=m['soc'],npu_arch='dav-3510',
       topology='A5-PoD-single-device-local-GM',memory_scope='local_GM',clock_hz=1000000000,
       clock_source='CANN GetSystemCycle: Ascend 950PR/950DT 1 GHz; see A5 testing guide')
Path('a5-profile.json').write_text(json.dumps(p,indent=2))
PY
cmake -S . -B build-a5 -DCMAKE_BUILD_TYPE=Release -DAKL_NPU_ARCH=dav-3510
cmake --build build-a5 --target akl_datacopy -j2
```

编译架构和系统计数频率分别依据官方 [dav-3510产品映射](https://gitcode.com/cann/asc-devkit/tree/master/examples/01_simd_cpp_api/03_basic_api/03_matrix_compute/mmad) 与 [GetSystemCycle说明](https://gitcode.com/cann/asc-devkit/blob/master/docs/zh/api/SIMD-API/基础API/工具接口/系统资源与变量/GetSystemCycle%28ISASI%29.md)：Ascend950PR/950DT为1GHz，A3为50MHz。仍需用目标机CANN版本的文档/头文件核对支持范围；若具体产品不属于上述范围，停止使用该profile，不从“A5 PoD”名称推断频率。

## 3. 先跑冒烟，再跑10个shape和真实shape

以下10个长度与A3曲线相同，两个方向共20个配置。固定uint32、DataCopy(params)、batch=1、loops=128，便于对齐比较；生产BF16等类型使用hook生成的候选，不替换dtype。

```bash
PYTHONPATH=python python3 - <<'PY'
from dataclasses import asdict
import json
from pathlib import Path
from akl.datacopy import CopyCase
sizes=[32,64,128,256,512,1024,4096,8192,14336,32768]
cases=[asdict(CopyCase(f'{d}_{n}',direction=d,api='DataCopy_params',
                      dtype='uint32',block_bytes=n,loops=128,batch=1,slots=1))
       for d in ('GM_UB','UB_GM') for n in sizes]
Path('ten-shapes.json').write_text(json.dumps(cases,indent=2))
Path('smoke.json').write_text(json.dumps([c for c in cases if c['block_bytes']==32],indent=2))
PY
npu-smi info
export DEVICE=0  # 仅在确认设备空闲后选择逻辑设备
python3 scripts/run_datacopy.py --profile a5-profile.json --device "$DEVICE" \
  --library build-a5/libakl_datacopy.so --cases smoke.json \
  --warmup 1 --samples 3 --output results/a5-smoke
python3 scripts/run_datacopy.py --profile a5-profile.json --device "$DEVICE" \
  --library build-a5/libakl_datacopy.so --cases ten-shapes.json \
  --warmup 3 --samples 20 --output results/a5-ten-shapes
python3 scripts/run_datacopy.py --profile a5-profile.json --device "$DEVICE" \
  --library build-a5/libakl_datacopy.so --cases "$SHAPES/candidate-cases.json" \
  --warmup 3 --samples 20 --output results/a5-real-shapes
PYTHONPATH=python python3 -m akl.datacopy_report results/a5-ten-shapes
PYTHONPATH=python python3 -m akl.datacopy_report results/a5-real-shapes
```

先检查shape报告中的告警与`unsupported.json`。每次使用新的输出目录。运行器会核对库/源码/环境，逐launch校验输出，并在运行前后检查设备占用；不能确认空闲时拒绝有效归档，不会中止其他任务。两次快照不能排除中途短暂干扰。

结果中看`report.html`、`latency.png`和`throughput.png`；`manifest.json`保存实际CANN/编译器等信息，`summary.csv`和`catalog.json`归档基线。10-shape完整运行共20×43=860次launch；每个点使用20次计时样本。

这里测的是每次完成延迟以及由它计算的有效吞吐，10点曲线不能确定上限。继续执行[吞吐扫描](throughput-scan.md)，按本机实际UB容量扩大单次长度，扫描batch=1～64，并做两轮平台检查。真实shape hook本身不测DMA独立完成时间，暂不能把复合TokenCopyToBuffer阶段直接标成纯DataCopy的预期时间。
