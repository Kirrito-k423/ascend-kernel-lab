# 同时观察延迟与有效载荷吞吐率

更新 DeepEP 和 AKL 子模块，重新完整编译。无需额外打开 DebugClock：latency profile
会采集两个字节计数并使用相同的 kernel latency 计算速度；关闭 profile 不分配计数 buffer。

## 运行与出图

以下为 Ascend950，保留实际实验使用的其他编译参数：

```bash
git submodule update --init --recursive
bash scripts/build.sh -soc_type Ascend950
export AKL_LATENCY_CLOCK_HZ=1000000000
export DISPATCH_CLOCK_DIR=/shared/exp01
bash scripts/run_v2_elastic_dispatch_precision_multi_node.sh
```

Atlas A2/A3 使用 `AKL_LATENCY_CLOCK_HZ=50000000` 和原芯片构建命令。
若还需要逐阶段时间线，编译时加 `DEBUG_CLOCK_ON=ON`。
全部节点成功后节点 0 自动解析；已采集的数据也可运行：

```bash
bash scripts/parse_profiling.sh /shared/exp01
```

输出 `dispatch_latency.csv`，并在 `plots/` 分开生成两张图（均提供 PNG 和 SVG）：

- `dispatch_latency_scatter`：延迟（us）。灰色 warmup 区、每 rank 均值红线、最快/最慢 rank 均值及实验均值继续保留。
- `dispatch_bandwidth`：有效载荷吞吐率（GB/s），两个面板分别展示处理和跨 rank 发送，每个点对应一个 rank 的一次 launch。
- `dispatch_latency_scatter_summary.json`：完整精度的统计，包括每轮最快/最慢 rank 列表、延迟及两类吞吐率。

延迟图新增蓝色点划线：**每轮先取所有 rank 的最慢延迟，再对这些最慢值求平均**。
它与“先求各 rank 的平均、再取最慢 rank”不同：两 rank 的耗时分别为 `[100,10]`、
`[10,100]` us 时，最慢 rank 均值为 55 us，每轮最慢值的平均为 100 us。
图上方同时标明这两个数值；X 轴下方按列对齐每次 launch 的最快、最慢、平均延迟，
最快/最慢单元格另标 rank；`+` 表示并列，完整并列列表见 JSON。表格包含 warmup，灰底标识。
跨轮统计排除 warmup，并使用 `--last-n` 选中的正式样本（默认最后 5 轮，`0` 表示全部）。
新统计只纳入所有 rank 都存在且均被选中的轮次；不完整列用 `*` 标识，表内仅统计已有 rank，
不纳入跨轮最慢均值；无完整选中轮次显示 N/A，JSON 为 null。

图例在图外换行，超过 128 rank 分页；延迟图每页最多 20 个 launch，保证表格可读，
均值与表格仍按全实验所有 rank 计算，其他页文件名带 `_pageN`。
带宽每 rank 先算总字节/总时间、再对 rank 等权平均，紫线标出实验值。
旧 CSV 没有字节列时只生成延迟图，不猜测数据量。

## 两种数据量

| 指标 | DeepEP 统计口径 |
| --- | --- |
| `processed_bytes` | 本 rank 实际输出的 token–expert 行数 × hidden × 输出元素字节数，包含 self 路由 |
| `sent_bytes` | 本 rank 实际提交的远程 token–expert 行数 × hidden × 输出元素字节数，不包含 self |

当前一个远程 WQE 对应一个 token–expert 行；同一 token 发往不同专家按实际次数计数。
Group-send 和 legacy 发送路径都使用原有已提交计数；接收侧复用不重叠 CumSum 区间计数。
只计算有效 x 载荷：BF16 每元素 2 字节，FP8 每元素 1 字节。
不包含 top-k 元数据、weight、padding、flag、协议头或重传，也不把收发流量相加。
这是整个业务区间上的有效载荷速率，不是 NIC 线速、峰值带宽或链路利用率。

## 计算方式与边界

每轮 `GB/s = bytes / (elapsed_us × 1000)`，使用十进制 `GB = 10^9 bytes`。
例如 1,000,000 字节用时 20 us，对应 50 GB/s。CSV 追加 `processed_gbps` 和 `sent_gbps`。
每 rank 的正式样本吞吐率按 **总字节数 / 总耗时** 计算，不能直接平均变长样本的 GB/s；
实验值对各 rank 的结果等权平均，不是集群总带宽。warmup 和 last-n 使用与延迟相同的筛选。
零耗时的速度留空；全部选中样本总耗时为零时摘要为 null，不输出无穷大。

默认 kernel 模式在入口屏障后开始，业务流水排空后、DebugClock flush 前结束。
计数字节换算、GM 写回与 Host 拷回放在结束时间之后。只增加低频标量累计，
不向等待轮询增加逐条计数或原子操作；仍不能宣称完全消除了插桩扰动。
`AKL_LATENCY_MODE=event` 使用整段 kernel 作为分母，包含屏障与统计写回，因此其 GB/s 口径不同。

AKL 通用接入：`KernelLatencyTimer(..., true)` 提供额外 `WorkData()`，在 kernel 完成后
向 `WriteKernelLatency` 传入两个累计字节数；会话结束前 `CopyWorkBytes` 取回逐轮数据。
Python `LatencyProfile(..., work=callback)` 的回调返回与延迟轮数一致的二元整数列表。
CSV/摘要保留 `work_kind` 口径；缺失或混用口径拒绝合并。原 32B/block 计时布局保留，
启用字节采集时再增加独立 32B/block，不占用业务 workspace。
