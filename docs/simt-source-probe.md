# 在 950 验证 SIMT 源码热点

本例用于回答：当前 CANN/msopprof 能否把一个 SIMT 函数关联到源码、汇编与停顿采样？
它是独立、可重复执行的合成探针，不访问 SQ/CQ、门铃或远端内存，不测真实 DeepEP 性能。
目前只有 CPU 模型和 Host 收集流程检查；CANN 编译、950 运行及采样映射等待你的结果。

## 1. 准备与运行

依赖目标机器已有的 CANN、支持 `-xasc` 的 Bisheng、CMake ≥3.20、Python ≥3.8；无 pip 依赖。
在本 PR 分支的仓库根目录执行。用本机实际路径加载 CANN，再确认空闲设备：

```bash
source /实际安装目录/set_env.sh
npu-smi info
# 修改为已确认空闲的 ACL 逻辑设备号；有可见设备映射时按映射后的编号选择。
SIMT_DEVICE=0
python3 scripts/run_simt_probe.py --device "$SIMT_DEVICE" --steps 4096 \
  --profile --output results/simt-950-first
```

脚本明确打印执行范围，记录环境，按 `-O2 -g --npu-arch=dav-3510` 构建独立例子，
先运行正确性基线，再调用已安装的 `msopprof`（或 `msprof op`）采集 `PcSampling`。
每条命令默认最多 180 秒，可用 `--timeout` 调整；失败/超时均保留日志，不自动重试。
只想先验证编译和正确性时，去掉 `--profile`。每次必须使用新的 `--output`。
采样可能内部重复启动 kernel；`--launch-count=1 --warm-up=0` 不代表只执行一次。
本例写入确定结果且输入只读，可以重复执行；不要把该命令直接换成真实通信算子。

## 2. 例子做了什么

一个逻辑 AIV 通过 `VF_CALL` 启动 `ProbeContexts` 的 32 个线程。
每线程从 4096 个 uint32 索引组成的表中追踪 `steps` 次，下一次访问依赖上次读取；
校验和写入 UB，完成依赖同步后复制到 GM，Host 独立校验全部 32 个结果。
输出 `PASS soc=... blocks=1 threads=32 table_words=4096 steps=... checked=32` 才表示该次校验通过。
这个工作集和循环是采样能力探针，不代表 `LoadSqCqContexts` 的缓存状态或真实耗时。

需要手工构建、单步排错时：

```bash
cmake -S examples/simt_source_probe -B build/simt-probe
cmake --build build/simt-probe -j2
build/simt-probe/akl_simt_probe "$SIMT_DEVICE" 4096
```

## 3. 查看结果与回传

脚本始终尝试生成 `results/simt-950-first.tar.gz`，包含 manifest、环境/版本日志、
源码快照、带调试信息的可执行文件/对象文件、编译命令及完整 profiler 输出。
请将回传包发回本任务；原始包可能含本机路径和设备信息，不要提交到公开仓库。
构建失败也直接回传包，先根据实际 CANN 编译日志修正；不要自行改成 `-O0`。
若超时，先确认该设备的任务已结束，再决定是否另开一次实验。

用配套 MindStudio Insight 打开包内 `profile/**/visualize_data.bin`：

1. 进入 Source，找到 `kernel.cpp` 的 `ProbeContexts`，点选 `pos = table[pos]`。
2. 核对是否联动到对应 PC/汇编，记录 `Stall Sampling` 数量、占比和停顿类别。
3. 回传截图，并说明：源码能否定位、汇编能否关联、目标函数是否有非零样本。

`captured_pending_review` 只表示采集命令成功且产生非空报告，不代表映射或样本覆盖通过。
`baseline_passed` 只表示未采样的输出校验通过；`failed` 看 manifest 中的错误和对应日志。
若目标函数没有样本，先回传结果；可在确认设备空闲后手动另测 `--steps 65536`，
但增加循环仅用于提高探针可观测性，不能用于推断真实函数的热点比例。
时间不可得时保留 `NA`；不要用停顿样本占比乘 kernel 总时间生成逐行微秒。

## 4. 本地验证与依据

无需 NPU 的收集流程检查：

```bash
python3 -m unittest discover -s tests -p 'test_simt_probe_runner.py' -v
```

本地还对实际 VF 函数体的 CPU 模型和 Host 校验路径做了地址/未定义行为检查，
覆盖 1～1048576 次循环、错误输入、ACL 失败和结果损坏；这些均不证明设备执行正确。
报告回来后，先确认工具链、函数映射及采样覆盖，再讨论真实多 rank 采集和网页接口。

- [SIMT 扩展与线程约束（CANN 文档）](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/910beta2/programug/Ascendcopdevg/docs/guide/编程指南/编程模型/AI-Core-SIMD编程/核函数.md)
- [固定版 msopprof：PcSampling、Source 及重放约束](https://github.com/Ascend/msopprof/blob/7e847da532c7fdfafa32af98bedd823a13e1ea23/docs/zh/user_guide/msopprof_user_guide.md)
- [固定依赖中额外预热的实现](https://github.com/Ascend/msopcom/blob/002ae2823b417d50b9786f10b2fb104677626e24/csrc/runtime/inject_helpers/ProfDataCollect.cpp#L981-L1026)
