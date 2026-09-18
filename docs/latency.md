# 多轮 latency 采集与绘图

通用 Python 会话在 `akl.latency`，ACL event 后端在 `akl/latency.h`，CSV 绘图在 `akl.latency_plot`。
当前已验证 CPU 替身、线程集合通信模拟、离线图形及 Python 打包；本次尚无真实 NPU 实测。

## 使用 ascend-deepep 的兼容入口

更新使用仓与固定子模块，然后按原芯片、top-k、RDMA 参数重新编译安装。
仅测 latency 无须开启 DebugClock；关闭采集可减少打点和写盘对连续实验的扰动。

```bash
git submodule update --init --recursive
source /usr/local/Ascend/cann/set_env.sh
DEBUG_CLOCK_ON=OFF EP_NUM_TOPK_IDX_BITS=32 bash scripts/build.sh -s 1 -soc_type Ascend950
export DISPATCH_RESULTS_DIR="$PWD/results/latency/exp01"
```

以上为 A5/32 位示例，其他配置使用既有业务参数。每组实验换一个输出目录。
各 rank 必须进入相同会话，并在同一 Host 线程执行相同次数的 dispatch：

```python
with buffer.profile_dispatch_latency(warmup=10):
    for iteration in range(30):
        run_one_dispatch()  # 替换为原有的一次 buffer.dispatch(...) 调用。
```

`warmup` 只标记前 10 次为预热，不代替调用者执行循环；以上留下 20 次正式测量。
也可传 `output_dir="/absolute/path/exp02"`，优先于环境变量。
默认目录依次取 `DISPATCH_RESULTS_DIR`、`DISPATCH_CLOCK_DIR`、当前目录下 `clock_results`。

rank 0 在会话结束后原子写入 `dispatch_latency.csv`，其余 rank 同步获知写入结果。
CSV 保留 rank、iteration、elapsed_ms、elapsed_us、is_warmup、in_average。
默认在计时前排入不计时的跨 rank 屏障，可显式设置 `synchronize_start=False`。
计时仅覆盖主 dispatch kernel，不包含 layout/notify、epilogue、D2H 或 CSV 写入。
需要同一线程/设备，不支持图捕获；所有 rank 必须参与，不能恢复已经退出的进程。

## 离线绘图

只需 Python、NumPy 和 Matplotlib；离线无需 torch/CANN/NPU。
使用仓新安装包会携带 akl，独立工具仓也可使用 PYTHONPATH：

```bash
export AKL_ROOT="/path/to/ascend-kernel-lab"
python3 -m pip install -r "$AKL_ROOT/requirements.txt"
PYTHONPATH="$AKL_ROOT/python" python3 -m akl.latency_plot   /path/to/exp01/dispatch_latency.csv --out-dir /path/to/exp01/plots
```

兼容旧命令 `python test/analyze_dispatch_time.py /path/to/exp01`。
输入实验目录时仍可读取旧 clock CSV；若发现多个 latency CSV，请明确选择实验目录或单个 CSV。
旧 clock 的负 cycle 会报错，不再静默置零；大于 500000 的有效 cycle 也保留。

输出包括：

- `dispatch_latency_scatter.png` 与同名 SVG：每 rank 样本、红色 rank 均值线、紫色实验均值线。
- `dispatch_latency_scatter_summary.json`：各 rank 均值和样本数、最快/最慢及实验值。
- 超过 128 rank 时继续生成 `_page2`、`_page3` 等 PNG/SVG；每页实验值均按全部 rank 计算。

图例独立放在图外，每行最多 8 项；每页最多 128 个 rank，避免遮挡或图片过长。
顶部标明最快/最慢 rank 均值，以及紫色实验均值；纵轴包含全部实际样本。
预热用叉号表示，不参与平均。默认每 rank 取最后 5 个正式样本；不足 5 个取全部。
传 `--last-n 0` 可取全部正式样本，`--last-n N` 取最后 N 个；窗口写入 summary JSON。
**实验值 = 各 rank 所选样本均值的算术平均**。
各 rank 样本数不等时仍等权；这不是最大 rank 延迟，也不是把所有样本混合后的加权平均。
重复 rank/iteration、非有限/负时延和冲突的预热标记会明确报错。

## 其他算子接入

`LatencyProfile(group, begin, end, abort, output, warmup=10, synchronize_start=True)`
接收会话后端回调：begin(rank, synchronize)、end() 返回毫秒样本、abort() 释放资源。
集合通信默认使用 torch.distributed，也可注入具有 get_rank/get_world_size/all_gather_object 的 collective。
C++ 后端使用 `akl::latency::BeginLatency`、`LatencyTimer::Start/Finish` 与 `EndLatency/AbortLatency`。
业务侧显式选定计时边界和起跑屏障，工具层不硬编码 workspace、AIV 数量或算子依赖。
