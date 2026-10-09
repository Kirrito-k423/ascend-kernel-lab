# A5 SIMD / REG 与 SIMT 同工作量实验

## 待验证的问题

规则连续 FP32 计算，一次操作的 SIMD 完成延迟是否比 SIMT 小？长依赖链中，把中间值留在向量寄存器内是否比每步读写 UB 更快？四条独立向量链是否能改善单链的吞吐？预先固定全部配置、独立随机两轮顺序，不按最快样本挑结果。

SIMT 的线程可通过 `i = threadIdx.x; i < n; i += Threads` 处理多个元素。本实验的 1 线程 / 129 元素用例直接检验该语义。线程数不是元素数上限。

| 实现编号 | 实现 | 依赖链中间结果 |
| --- | --- | --- |
| 0 | SIMD Tensor API，每步 PIPE_V barrier | 每步 UB 读写 |
| 1 | SIMT，1/32/128/512/1024/2048 线程 | 每线程寄存器 |
| 2 | REG SIMD，一条向量链 | 向量寄存器 |
| 3 | REG SIMD，四条独立向量链交错 | 向量寄存器 |

REG 属于 SIMD 编程，不是另一套硬件。向量宽度使用同版本 `GetVecLen()`；dav-3510 FP32 为 64 lane。四组版在不足四组时回退单组尾块处理，所以小 shape 两者接近是预期行为。

计时：预先 GM→UB，并完成 MTE2 同步；从分发前读 SYS_CNT，到计算与 V_S 完成等待后读 SYS_CNT。GM 搬入/导出、Host launch 和 oracle 不计入设备区间。保留原始区间，不减空对照。steps=0 统一使用加法入口，记录该实现的控制/加载写回/完成等待，不代表其他运算专属的控制开销（Tensor 只有等待）；不能把不同实现的空对照直接互减当硬件指令时延。

## 范围与正确性

单 AIV、本地 UB、FP32、加减乘除，15 个常规/尾块 shape（1–16384）。主矩阵 steps=1/16/128，2048/8192 额外 steps=512；10 个批量 shape 补 steps=0。1588 配置，两轮独立随机；每配置 2 次预热 + 15 次计时。输入逐元素变化，输出完整比较 CPU 每步 FP32 oracle，容差 `3e-5 * max(1, abs(expected))`；尾块之外初始值及 GM 128B 保护区不得改变。

使用 CANN 9.1.0、dav-3510，`-O2 -fno-fast-math`。最大两份 UB 数据 131072B + 32B 计时，动态 UB 在 launch 明确预留；编译器栈、Data Cache 另有余量。负载为依赖链的有效元素运算数 `n*steps`，吞吐 `n*steps / 时间`，不声称 HBM 带宽或整芯片 FLOPS 上限。四组版仍可能受循环、除法展开与数据搬运限制。

## 复现

```bash
source /usr/local/Ascend/cann/set_env.sh
cmake -S examples/a5_simd_mbench -B build-simd -DCMAKE_BUILD_TYPE=Release
cmake --build build-simd -j2
python3 scripts/plan_simd_arithmetic.py --output results/simd-plan

# results/setup/profile.json 使用目标环境的已核实时钟 profile。
# 每次前后检查整机占用；可用 --chunk-size 128 合并短批次；默认每 16 配置一个 chunk。占用变化时保留失败包，另择空档。
python3 scripts/run_simd_batch.py --device 1 --round 1 --start 0 --count 4 \
  --output results/simd-r1-b0
```

默认每轮 100 个 chunk，依次 start=0,4,...,96；若生成计划时使用 --chunk-size 128，则每轮 13 个 chunk，依次 start=0,4,8,12（最后 count=1）。生成的 plan.json 记录配置与随机种子。实际执行前查看 npu-smi，不中止其他工作。每个 chunk 的 plan、原始 tick/预热/Host 时间、oracle 最大误差、前后占用、源码与二进制哈希、CANN/编译器和时钟 profile 都保留；只有 parent batch 明确列为 validated 的 chunk 可进入公开汇总。本轮先以 16 配置短批验证，再将未测配置按原随机次序合并为至多 128 配置的短批，减少设备初始化/退出；不重复或筛选已测配置。借用机器的快照不能证明采样间每一瞬间均无外部活动。

官方依据：[SIMT 编程模型](https://www.hiascend.com/document/detail/en/CANNCommunityEdition/910/programug/Ascendcopdevg/docs/en/guide/programming_guide/programming_model/ai_core_simt_programming/overview.md)、[SIMD HelloWorld](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/910/programug/Ascendcopdevg/docs/guide/入门教程/快速入门/基于SIMD编程/HelloWorld.md)、[VF 融合优化](https://www.hiascend.com/document/detail/en/CANNCommunityEdition/910/programug/Ascendcopdevg/docs/en/guide/operator_practice/simd_operator_optimization/vector_compute/vf_optimization/vf_fusion_optimization.md)。调用签名另核对目标 CANN 9.1.0 的 Reg API 本地头文件。

## 实现状态

已在 Ascend950DT_9582 / CANN 9.1.0 完成实机验收：1588 配置 × 两轮、47640 计时样本、6352 预热样本。逐元素输出、尾块与保护区全部通过，最大绝对误差 5.48363e-6；两轮 p50 相对差异中位数 0%，最大 1.8519%。

8192 元素各一次加减乘除，Tensor API 相对最优测试 SIMT 快 4.28–4.80 倍。2048/8192 元素 × 128 步加减乘依赖链，REG 4 组相对最优 SIMT 快 4.08–7.55 倍；除法没有普遍的 REG 优势。本轮全部模式在新二进制内重新测量，不拼接旧工程数据。

[交互曲线和原始样本](https://kirrito-k423.github.io/micro-benchmark-lab-web/?lab=simd) · [完整对照报告](https://github.com/Kirrito-k423/micro-benchmark-lab-web/blob/codex/a5-mbench-results/reports/a5-simd-20261009.md)
