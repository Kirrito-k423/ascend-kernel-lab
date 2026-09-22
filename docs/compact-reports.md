# 多机自动生成 latency 图和打点报告

## 一次运行，自动出图

更新使用仓的 AKL 子模块后重编译开启打点的版本。沿用原芯片、top-k、RDMA 参数；
以下为 A5 / 32 位索引示例：

```bash
git submodule update --init --recursive
DEBUG_CLOCK_ON=ON EP_NUM_TOPK_IDX_BITS=32 bash scripts/build.sh -soc_type Ascend950
export AKL_LATENCY_CLOCK_HZ=1000000000  # Ascend950；Atlas A2/A3 使用 50000000
bash scripts/run_v2_elastic_dispatch_precision_multi_node.sh
```

脚本仍使用配置区的节点和环境，默认开启 latency profile。所有节点成功结束后，
在节点 0 的同一 Python 环境中统一解析共享目录一次。绘图需要该环境安装 matplotlib。

默认输出到 `LOG_DIR/clock/<本次运行ID>/`；`--log-dir` 同时影响默认报告根目录。
可在运行前设置 `DISPATCH_CLOCK_DIR` 指定共享目录；每组实验使用独立目录。
`DISPATCH_RESULTS_DIR` 默认同目录，也可另设 latency CSV/图片目录。

- `plots/dispatch_latency_scatter.png`：排除 warmup 后，使用全部测量轮次计算均值。
- `result/index.html`：选择 launch 和 rank；各轮有独立汇总 HTML 和 Chrome/Perfetto JSON。
- `result/launches/launchN/runs/rankR-pidP-launchN/`：每 rank 的 HTML、SVG、trace.json。

主程序失败不会解析；解析失败会令脚本返回非零。关闭 profile 或打点时，缺少的报告
明确跳过。主程序和后处理沿用受管进程组，后处理日志单独保存在 LOG_DIR 下。

## 默认只留最后一次 launch

不设置环境变量就滚动保留每个 rank/PID 的最新完整采集。
需要保留所有轮次时，在启动所有节点前设置：

```bash
export AKL_TRACE_KEEP_LAST=0
```

`1` 恢复只留最后一次。新采集写入完成后再删除上一份；写失败或损坏记录不替换旧采集。
只管理本进程的文件，不扫描以前进程的数据，也不删除业务文件。
每轮仍会同步拷回和写入；此策略节省磁盘，不消除打点或 D2H 耗时。

## 单独解析已有数据

统一解析 latency 和打点报告（可用第二个参数指定独立 latency 目录）：

```bash
bash scripts/parse_profiling.sh /absolute/path/exp01
```

只解析打点并筛选最后一轮：

```bash
export AKL_ROOT="$PWD/3rdparty/ascend-kernel-lab"
PYTHONPATH="$AKL_ROOT/python" python3 -m akl.semantic \
  /absolute/path/exp01 --source "$PWD/kernels/elastic_dispatch.cpp" --last-launch
```

`--last-launch` 仅选各实验编号最大的 launch 并保留其全部 rank；缺少此前出现的 rank 时
报错并保留输入。不加此参数则解析全部已存在的轮次，不猜测未出现过的预期 world size。
默认最多 64 个进程，手动解析用 `--jobs 8` 调整，多机脚本用 `AKL_PARSE_JOBS=8` 调整。

默认全部成功且报告发布后删除原始 trace.bin/capture.json 和旧派生副本；失败保留输入。
仅最后一轮模式成功后也清理被跳过的旧轮次。自动解析采用相同规则。
需要手动解析或留原始数据时，用 `AKL_AUTO_PARSE=0` 关闭自动解析；首次手动解析加
`--keep-intermediates` 可保留原始文件以便重新映射源码。
结果只保留一套 HTML/SVG/JSON，不再默认生成 JSONL、counts.json 或任何 ZIP。
`--zip-launches` 可选生成独立轮次 ZIP；始终不生成顶层总 ZIP。整个 result 可离线复制打开。

复现 CPU 合成对照：准备 old/python/、new/python/ 快照，运行
`python3 tests/benchmark_batch.py /absolute/scratch`。脚本保存指标，测量后清理自己生成的数据。
