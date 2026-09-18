# 默认测两份：DebugClock ON / OFF

`akl.latency_compare` 复用同一个构建命令和业务命令，自动执行：
ON 编译/安装 → 新进程采样 → OFF 编译/安装 → 新进程采样 → 两张图与差值。
原 `profile_dispatch_latency` 是单次会话，保持兼容；成对测量从这个新入口启动。
不会在同一个已加载库的进程中切宏，也不会暗中重放有状态算子的 Python 循环。

## ascend-deepep 使用

更新使用仓及固定 AKL 子模块。进入已配置 torch/torch_npu/CANN 的独立开发环境，
沿用已有芯片、top-k、RDMA 参数。先准备好匹配的 SHMEM；下面为 A5/32 位索引示例：

```bash
git clone --branch codex/latency-ab-compare https://gitcode.com/shaojiemike/ascend_deepep.git ascend_deepep-latency
cd ascend_deepep-latency
git submodule update --init --recursive
source /usr/local/Ascend/cann/set_env.sh
export EP_NUM_TOPK_IDX_BITS=32
export AKL_ROOT="$PWD/3rdparty/ascend-kernel-lab"
python3 -m pip install -r "$AKL_ROOT/requirements.txt"
PYTHONPATH="$AKL_ROOT/python" python3 -m akl.latency_compare \
  --output "$PWD/results/latency/exp01" \
  --build 'bash scripts/build.sh -soc_type Ascend950' \
  --run 'bash /absolute/path/run_latency_case.sh'
```

把 `--run` 换成原有的**前台、等待所有 rank 结束**的业务启动命令，不在末尾加 `&`。
不需要手工切换开关、目录或分别画图；`--output` 必须是不存在的新目录，省略则自动生成时间戳目录。
构建命令必须读取 `DEBUG_CLOCK_ON=ON/OFF` 和 `BUILD_DIR`，不要写死开关或仅复用旧库。
正常完成后当前安装保留 OFF 版本；ON/OFF 的构建产物分别留在各自 `build/`。

业务脚本在所有 rank 各执行一次相同会话，固定输入、随机种子、轮数、设备和通信配置：

```python
with buffer.profile_dispatch_latency(warmup=10):
    for iteration in range(30):
        run_one_dispatch()  # 换成原有业务调用；不是自动生成输入
```

10 轮 warmup 和 20 轮正式采样都会保留。默认统计各 rank 最后 5 个正式样本；
命令加 `--last-n 0` 统计全部正式样本，`--last-n N` 选最后 N 个，两组使用同一窗口。
同一次子进程运行只能写一个 latency 会话，第二次写入会报错，不覆盖第一份。

## 输出

```text
exp01/
├── latency_on.png / .svg            # 带打点：各 rank 均值 + 实验均值
├── latency_off.png / .svg           # 无打点：同一纵轴，便于比较
├── latency_on_summary.json / latency_off_summary.json
├── comparison.json                 # 两个实验值、差值、百分比、命令、状态、CSV 哈希
├── trace_on/
│   ├── dispatch_latency.csv        # 所有 rank/轮次，含实际 trace_mode
│   ├── build.log / run.log
│   ├── build/
│   └── clock/                      # ON 版原始 DebugClock 数据
└── trace_off/                      # 独立 CSV、日志和构建，无设备打点导出
```

每 rank 一条红色均值线，实验均值为紫线；最快/最慢/实验值均标数值，warmup 灰底，
图例在图外换行。超过 128 rank 自动分页；两组 rank 顺序和纵轴一致。
`delta_us = ON实验值 − OFF实验值`，百分比为 `delta_us / OFF实验值 × 100`。
OFF 为零时百分比为 null；负差值原样保留，不假定插桩一定更慢。
ascend-deepep 的 latency 只计主 dispatch kernel，包含设备打点/Flush，
不含 Host D2H、文件写盘、编译安装或 Python 端到端时间；顺序两次实验仍有环境波动。

## 多机和其他算子

该入口在一个控制进程执行一次。多机 `--build` 必须完成所有机器的构建/安装；
`--run` 必须等待所有节点完成，并向各 rank 传递 `AKL_TRACE_MODE`、
`AKL_LATENCY_OUTPUT_DIR`、`AKL_TRACE_DIR`，将 rank 0 的 CSV 放在控制机可见目录。
AKL 不猜测机器清单或 SSH 凭据；已有调度脚本负责节点启动和环境转发。

其他算子接入共享 `LatencyProfile(..., trace_enabled=callback)`，callback 从实际加载库
返回编译态 bool。编译开关变量可用 `--build-flag` 改名，CSV 名可用 `--csv-name` 改名。
会话自动把 CSV 放进 `AKL_LATENCY_OUTPUT_DIR`，覆盖该次会话传入的输出目录。
使用仓打点导出优先读取 `AKL_TRACE_DIR`，原 `DISPATCH_CLOCK_DIR` 仍可单独使用。

所有 rank 的编译态需一致并匹配预期；缺少编译态、旧库、缺 CSV、rank/轮次/warmup
不匹配、构建或运行失败都会中止，保留日志和 `status=failed`，不伪造一对成功结果。
`--timeout 秒` 可限制每条构建/运行命令；中断只清理本次命令的进程组。
