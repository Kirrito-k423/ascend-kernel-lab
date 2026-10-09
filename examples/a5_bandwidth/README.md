# A5 单卡多 AIV DataCopy 带宽

## 问题与判定标准

同一设备上多个 AIV 使用本地 GM，多少核能达到本实现观测带宽的平台？GM/缓存/内存控制器为同一设备资源，各核使用自己的 UB。分区读、分区写与同址只读分开；无竞争同址多写未使用。

预先固定 14 档核数（按运行时 AIV 上限裁剪），两轮独立随机曲线与核数顺序。每条曲线的核数点在同一进程复用同一组存活 GM 分配。N95 为已测档位中最少核数达到该曲线两轮 p50 对应带宽最大值的 95%。另检查相邻后续点是否同在 95% 范围；孤立峰值不称平台。它是本实现/本设备/本工作集的观测饱和点，不直接宣称硬件规格带宽已用满。

## 矩阵

方向 GM→UB / UB→GM，uint32 原样搬运，`DataCopy(LocalTensor, GlobalTensor, count)` 或反向重载，所有地址与长度 32B 对齐。每核两组 UB 窗口，复用前等待该窗口的旧 DMA；一组 batch 个连续 tile，末尾排空两组。

| 变量 | 值 |
| --- | --- |
| AIV | 1/2/4/8/12/16/20/24/28/32/40/48/56/64，实际可用上限来自 ACL |
| tile × batch | 4KiB × 1、4KiB × 8、32KiB × 2、64KiB × 1 |
| 分区请求总工作集 | 4MiB、1GiB；追加 2GiB 验证 |
| 每次请求总搬运量 | 首阶段 2GiB，2GiB 工作集阶段 4GiB；按核数与两组 batch 向上取整，实际字节数逐 case 记录 |
| 同址只读对照 | 128KiB，32KiB × 2，14 核数；重复逻辑字节不等于 HBM 物理流量 |
| 空循环/事件对照 | 两方向、32KiB × 2、4MiB；不扣除，不声称搬运带宽 |

此目标环境：ACL 查询可用 AIV=64、UB=221184B；同版本 Ascend950DT_9582 平台配置 L2=128MiB。1GiB/2GiB 请求工作集为该配置 L2 容量的 8/16 倍。没有缓存计数器证据，称“大工作集 GM 带宽”，不宣称物理总线流量。请求工作集按每核两组对齐，实际大小略有变化，公开数据保留真实值。

## 计时与正确性

聚合吞吐 = 全核有效搬运总字节 / **同一 stream 的 ACL begin/end Event 共同区间**。区间包含 kernel 调度、UB 初始化、起止 SyncAll、DMA、完成等待及少量读出/计时导出；不含 Host upload、输出下载和 oracle。总搬运量大于等于 2GiB，降低这些固定开销占比。不能相加各核峰值推导整卡吞吐。

每核另记录 DMA 主体前后 `GetSystemCycle()` 原始整数，单核差值只用于负载均衡观察，不用未校准跨核绝对起点拼接聚合时长。起跑和主体结束使用硬件 `SyncAll()`；只启动不超过运行时可用 AIV 的 block 数。

每 launch 均验证全部写目标（每核 pattern 随 launch 变化），或全部核最后两组读入结果（输入按全局位置变化），以及 128B GM 保护区和每核计时提交。读中间 tile 不逐项导出，以免给被测路径添加处理负载；DMA API 调用与完成链保留。参数检查保证每核至少遍历一整圈分区地址；所有 warmup 同样验证。

## 复现

```bash
source /usr/local/Ascend/cann/set_env.sh
# 在新的实验目录执行；不要覆盖已有 setup、plan 或结果。
python3 scripts/prepare_bandwidth.py --device 1 --clock-hz 1000000000 \
  --clock-source 'CANN GetSystemCycle: Ascend950PR/950DT SYS_CNT 1 GHz' \
  --output results/setup
cmake -S examples/a5_bandwidth -B build-bandwidth -DCMAKE_BUILD_TYPE=Release
cmake --build build-bandwidth -j2
BANDWIDTH_CORES=$(python3 -c 'import json; print(json.load(open("results/setup/environment.json"))["aiv_count"])')
python3 scripts/plan_bandwidth.py --max-cores "$BANDWIDTH_CORES" --output results/bandwidth-plan
python3 scripts/run_bandwidth_batch.py --device 1 --round 1 --start 0 --count 2 \
  --output results/bandwidth-r1-b0
```

本例 SYS_CNT 值只适用于已有依据的 Ascend950；它不是核心频率。准备脚本实际查询 AIV/UB，L2 来自当前 CANN 的匹配 SoC 配置；不把其他机器的容量复制过来。构建后自动生成二进制/三份 C++ 构建输入/工具链凭据，运行前检查源码与二进制一致。

首批通过后，继续 round 1 的 start=2/4/…/26，再完成 round 2 的 start=0/2/…/26；末批只有一条曲线。每次使用新的输出目录并检查返回码。任一曲线占用检查或正确性失败就停止，保留失败目录和已经验收的曲线，重新确认空闲后只补缺失曲线，不重放未知远程任务。

64 AIV 时首阶段 266 配置、19 曲线/轮；随后因最高核数尚未出现清楚平台，追加 112 配置、8 条 2GiB 曲线/轮。共 378 配置、27 曲线/轮，2 次预热 + 12 次计时，两轮共 9072 个计时样本。后续配置编号追加，不覆盖首阶段原始结果；被测二进制保持一致。每曲线前后检查整机 npu-smi；有外部进程时停止剩余曲线，不中止他人的工作。结果目录必须不存在。保留源码、二进制、工具链、plan、raw samples、占用前后快照。

当前计划生成器保留实际两阶段顺序：前 19 条曲线使用 seed=20261009+round，追加 8 条使用 seed=20261010+round；每个阶段独立随机曲线和核数。接收端以原始 plan 验证配置，不只按最终图表顺序复现。

官方依据：[ACL Event 共同区间](https://www.hiascend.com/document/detail/en/CANNCommunityEdition/910/API/runtimeapi/aclcppdevg_03_0090.html)、[SyncAll](https://www.hiascend.com/document/detail/en/CANNCommunityEdition/910/API/ascendcopapi/docs/en/api/SIMD-API/basic_api/sync_control/inter_core_sync/SyncAll.md)。SyncAll/动态 UB 另核对实际 CANN 9.1.0 头文件。此工程只面向 dav-3510。

## 实现状态

2026-10-09 在 Ascend950DT_9582 / CANN 9.1.0 完成 378 配置 × 两轮、9072 计时样本及 1512 预热样本；全部输出、保护区和逐核提交验证通过，接收端完整矩阵、源文件/构建/样本哈希与占用证据验收通过。两轮 p50 相对差异中位数 0.162%，最大 6.153%。

1GiB 工作集的观测最高读 2.076TB/s、写 2.099TB/s，均为 64 AIV、4KiB × 8，相对单核 54.46/55.15 倍；56→64 核仍提高 13.36/14.32%。2GiB 复验最高读 2.099TB/s、写 2.089TB/s，同样未确认平台。全部大工作集曲线的 N95=64；它仅是已测档位的观测阈值，当前不能给出已用满硬件带宽的核数。

[完整实测报告](https://github.com/Kirrito-k423/micro-benchmark-lab-web/blob/codex/a5-mbench-results/reports/a5-bandwidth-20261009.md) · [交互曲线、表格与原始样本](https://kirrito-k423.github.io/micro-benchmark-lab-web/?lab=bandwidth)
