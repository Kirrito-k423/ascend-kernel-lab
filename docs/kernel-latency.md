# 屏障内 latency：只测两个外层同步之间的工作

DeepEP 当前在主 kernel 入口、出口调用跨卡屏障。ACL event 包住整个 kernel，
所以会包含两端屏障等待。新的 `kernel` 模式保留屏障，在入口屏障返回后读起点，
业务流水排空后、DebugClock flush 前读终点；trace 导出、latency 写回和 Host 拷回都在区间外。

每个 rank 的 latency = `max_AIV(end_tick - start_tick) / SYS_CNT频率`。
先在每个 AIV 内做整数减法，不相减跨卡或不同 AIV 的原始时间戳。
它是屏障内最慢 AIV 的区间耗时，不是整个分布式调用的墙钟耗时，也不保证屏障释放绝对同时。
业务内部等待、初始化及开启 DebugClock 后的逐次打点开销仍计入。
trace 的 UB 重置、flush 及其后同步不计入；`event` 对照模式仍包含这些工作。

## 使用

更新使用仓的子模块并重编译，沿用原芯片和 top-k 配置。无需开启 DEBUG_CLOCK_ON。
在所有节点设置相同的计时模式和系统计数器频率，然后使用原来的 `--profile` 流程：

```bash
export AKL_LATENCY_MODE=kernel              # 默认
export AKL_LATENCY_CLOCK_HZ=1000000000      # 仅 Ascend950：1 GHz
bash scripts/run_v2_elastic_dispatch_precision_multi_node.sh
```

Atlas A2/A3 的 SYS_CNT 按官方说明使用 `50000000` Hz。这些数值是目标芯片的
系统计数器频率，不能使用 AI Core 主频，也不能沿用可视化的 cycle→us 默认值。
未提供有效频率时，profiling 会话在启动被测 kernel 前集体报错，不猜测换算系数。

依据：[CANN 9.1 系统计数器说明](https://www.hiascend.com/document/detail/en/CANNCommunityEdition/910/API/ascendcopapi/docs/en/api/SIMD-API/C-API/sys_var/asc_get_system_cycle.md)。

对照原来的整段 kernel 耗时：

```bash
export AKL_LATENCY_MODE=event
# 使用另一组实验输出目录再运行；event 模式不需要 AKL_LATENCY_CLOCK_HZ。
```

Python `profile_dispatch_latency(..., synchronize_start=True)` 入口保留。
当前业务 kernel 的入口/出口屏障始终执行，`synchronize_start=False` 不会关闭它们。
不启用 profile 时不分配 latency buffer、不读取计时 tick。

## CSV 与图片

`dispatch_latency.csv` 的原有 elapsed_ms/us、warmup 和平均标记保留，追加
`measurement`、`clock_hz` 列。旧解析器可继续使用原列，新图片明确显示计时边界；
混合模式或不同频率的 CSV 拒绝合并，防止把不同口径误算成同一个实验均值。

```bash
PYTHONPATH="$AKL_ROOT/python" python3 -m akl.latency_plot \
  /path/to/dispatch_latency.csv --skip-clock --last-n 0
```

已完成 CANN A5 编译和 CPU 合约/CSV/绘图回归。另在 A2 910B2 上实测 1/8 AIV：
区间外增加合计约 2 ms 等待时，核内计时保持约 100/114 us，event 计时增加约 2 ms。
这是受控等待探针，不是 DeepEP SHMEM 屏障或 A5 多卡实测；后者的起点偏差与扰动仍待验收。

## 复现 A2 设备探针

测试辅助 PR 提供 `tests/kernel_latency_probe.cpp`。先加载 CANN，确认目标设备空闲。
以下例子用设备 0；替换为实际空闲设备。探针输出含预热，比较时丢弃前 3 轮。

```bash
bisheng -O2 -std=c++17 -xasc --npu-arch=dav-2201 \
  -Iinclude -I"$ASCEND_HOME_PATH/include" tests/kernel_latency_probe.cpp \
  -L"$ASCEND_HOME_PATH/lib64" -lascendcl -lruntime \
  -Wl,-rpath,"$ASCEND_HOME_PATH/lib64" -o /tmp/akl-latency-probe
AKL_LATENCY_CLOCK_HZ=50000000 /tmp/akl-latency-probe 0 > probe.csv
```
