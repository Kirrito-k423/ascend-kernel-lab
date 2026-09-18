# 基础 DebugClock：按 launch 查看

首版只记录字符串事件名和绝对 cycle，不记录处理量、累计 count 或速度。
同一 AIV 的相邻打点构成一个阶段；HTML/SVG/Chrome JSON 每核一行，不画父路径堆栈。
重复阶段逐次保留，`occurrence` 表示第几次命中，不是处理的数据个数。

## 使用仓采集

更新 PR #20 和固定子模块后重新编译；以下为 A5、32 位 top-k 示例：

```bash
git submodule update --init --recursive
source /usr/local/Ascend/cann/set_env.sh
DEBUG_CLOCK_ON=ON EP_NUM_TOPK_IDX_BITS=32 bash scripts/build.sh -s 1 -soc_type Ascend950
export DISPATCH_CLOCK_DIR="$PWD/results/clock/exp01"
# 用原来的启动命令运行业务；每台机器的所有 rank 都应收到此环境变量。
```

每组实验使用独立目录；各机器的数据集中到该实验目录下，再统一解析：

```bash
export AKL_ROOT="$PWD/3rdparty/ascend-kernel-lab"
PYTHONPATH="$AKL_ROOT/python" python3 -m akl.semantic "$DISPATCH_CLOCK_DIR" --source "$PWD/kernels/elastic_dispatch.cpp"
```

打开 `result/index.html` 选择 launch。每个 `result/launches/launchN/` 包含：

- `index.html`：当前 launch 的 rank 汇总、单次时间线切换。
- `trace.json`：只含当前 launch 的全部 rank，可直接导入 Perfetto。
- `summary.json`、`events.jsonl`：该 launch 的统计和逐条记录。
- `result.zip`：该 launch 的完整独立下载包，解压后打开 `result/index.html`。

每个原始 rank 目录也保留自身 HTML/SVG/JSON。顶层不再生成跨 launch 的 trace.json；
顶层 `result.zip` 仍可下载全部结果。不同实验子目录独立分组；同 rank 多 PID 会提示歧义。
launch 为进程内编号，跨 rank 同号须由实验调用顺序保证；各 rank 时钟未校准。

典型阶段为初始化、URMA 专家 ID 拷贝、mapping、队列、send-loop、累加核的计数流程、
接收端 `LW:wait`/`LW:copy`。发送阶段在每个 group/chunk 都记录；接收端按完成批次记录，
不会在每次未就绪轮询中写点。send-loop 不是远端收齐完成，copy 阶段包含已有清理同步。

每核容量默认 256，满后明确报告 dropped；计时显示默认 0.001 µs/cycle，可配置但不是时钟校准。
本版回到两槽 ABI v1。此前 count 试验版 v2 数据不由该首版解析，需保留对应旧工具或重新采集。
多轮 latency CSV 与绘图接口保持不变，见 [latency 使用指南](https://github.com/Kirrito-k423/ascend-kernel-lab/blob/codex/latency-plot-tests/docs/latency.md)。
