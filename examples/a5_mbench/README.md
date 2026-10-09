# A5 插空微基准：长度对齐与 SIMT 算术

本轮回答两个问题：DataCopy 搬运 128 和 256 之间的长度有何代价；SIMT 相对相同工作量的 Scalar/SIMD 有何加速，以及 VF 调用随线程数变化的开销。

## DataCopy

运行 `python3 scripts/plan_alignment.py --output results/alignment-plan` 生成短批次。uint8 与 float32 均扫描 127–257 个元素，同时保留元素数和字节数。小工作集、单 AIV、loops=8192、batch=1，单窗口与双窗口独立测量；每配置预热 2 次、12 个原始计时样本与 12 个关闭计时对照，重复两轮。

主曲线保持 GM 起点对齐，用同一 DataCopyPad 重载比较长度。地址实验在边界长度上改变 GM 起点，单列曲线；32B 对齐的长度另测 DataCopy(params)，以观察重载差异。不能将不同重载的差异直接归因于长度对齐。两个方向都检查 payload、间隔与输出保护区；UB 末尾 padding 不赋予值语义。

## SIMT

FP32 加、减、乘、除均按每元素依赖链执行；输入、输出落盘校验，准备数据与导出位于计时之外。Scalar、SIMD、SIMT 使用相同输入、元素数与迭代数。端到端设备区间包括相应计算调用和完成等待；同工作量速度比保留调用开销，不宣称纯 ALU 指令吞吐。

元素数为 32/128/512/2048/8192；每元素执行 1/16/128 次运算；SIMT 线程数及 launch bound 均为 1/8/16/32/64/128/256/512/1024/2048。Scalar 每元素逐步运算；SIMT 将该元素的依赖链保留在寄存器中；SIMD Tensor API 每次迭代从 UB 读写，并保留依赖同步。因此速度比包括这些实现方式的差异，不是硬件浮点单元的直接对比。不使用 fast-math；每一步 CPU FP32 参考及输出保护区均检查。

线程开销单独测量：每次 VF 为每线程执行一次可观察 UB 写出，分别测若干 VF 调用总完成耗时，显示每调用原始值及随调用次数的斜率。该指标包含 VF 调用、最小线程体与完成同步；不能声称硬件线程创建的独立精确时间。另保留不调用 VF 的计时对照，原始耗时不盲减。

## 借用设备

通过本机 SSH 任务中台选择 A5；每批开始前用 npu-smi 确认空闲。单 AIV 测量采用独立目录，按短批次保存原始结果；若出现其他进程，则停止后续批次并保留已完成文件，不终止他人任务。构建限制并行度，运行设超时，结果从 SHW_RESULTS_DIR 回收。完整机器日志仅在被忽略的 results/ 中保存，网站只导入脱敏的参数、原始 tick、统计量和证据哈希。

## 复现

在 CANN 9.1.0 / dav-3510 环境中，先加载现场 `set_env.sh`。用独立源码目录执行以下准备命令；时钟值与依据由调用方明确提供，本次使用 Ascend950 的 SYS_CNT 1 GHz：

```bash
bash scripts/build_a5_mbench.sh 1 1000000000 \
  'CANN GetSystemCycle: Ascend950PR/950DT SYS_CNT 1 GHz'
```

脚本先检查整机空闲，再保存 SoC、实际 AIV/UB 容量、CANN 安装信息、编译器、构建哈希及两种冒烟结果。通过后逐批运行，每次使用新的输出目录：

```bash
python3 scripts/run_borrowed_batch.py --kind alignment --device 1 \
  --round 1 --start 0 --count 4 --output results/alignment-r1-b000
python3 scripts/run_borrowed_batch.py --kind simt --device 1 \
  --round 1 --start 0 --count 4 --output results/simt-r1-b000
```

DataCopy 有 47 个 chunk，SIMT 有 48 个；将 start 依次设为 0/4/8…44，再完成 round 2。借用环境里可串行测量，也可在两个经确认空闲的节点分别测量；同类实验固定节点和设备。占用检查失败时保留原始目录，排除受影响 chunk，只在重新确认空闲后以新目录补齐。未知任务只查询原 ID。

接收端使用网站仓的 `scripts/import-a5-mbench.py`，再次核对完整两轮、源文件/构建/样本哈希、输出校验和占用检查，再重算 p50/p95。`--allow-partial` 仅供本地预览，正式导入缺少配置时会失败。

## 动态 UB

该版本现场头文件 `dav_3510/kernel_tpipe_impl_c310.h` 中，TPipe 的 UB 地址从 `GetDynamicMemStartPos<Hardware::UB>()` 开始。启动 SIMT 混合内核时按 `2 × n × sizeof(float) + 32` 申请动态 UB，用于两个 FP32 缓冲与计时缓冲；容量随实验参数计算。不能将动态 UB 配成 0 后直接使用较大 TPipe 缓冲。预留栈和 Data Cache 由编译器/运行时管理。

## 依据

- [官方 DataCopyPad GM→UB](https://www.hiascend.com/document/detail/en/CANNCommunityEdition/910/API/ascendcopapi/docs/en/api/SIMD-API/basic_api/memory_vector_compute/data_move/DataCopyPad_GMToUB.md)：GM 与 UB 起点、每块长度的对齐约束；实际签名以现场 CANN 头文件为准。
- [官方 SIMT 核函数配置](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/910/API/ascendcopapi/docs/api/SIMT-API/SIMD与SIMT混合编程简介/扩展语法/核函数配置-147.md)：线程上限影响寄存器预算；每个实例的 launch bound 与实际线程数保持一致。

状态：计划及运行代码准备中。NPU 正确性和性能只有在回收原始测量并核验后才标记 validated。
