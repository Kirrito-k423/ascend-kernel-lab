# 64 rank 批量解析：并行、精简输出、只留最后一轮

## 已有采集：默认并行解析

更新 AKL 后，离线解析无需重新编译 kernel：

```bash
export AKL_ROOT="$PWD/3rdparty/ascend-kernel-lab"
PYTHONPATH="$AKL_ROOT/python" python3 -m akl.semantic \
  /absolute/path/exp01 --source "$PWD/kernels/elastic_dispatch.cpp"
```

默认最多 **64 个进程**，每个采集独立解码、渲染；`--jobs 8` 可降低 CPU/内存占用，
`--jobs 1` 可串行排查。64 是并行度上限，不是写死的 rank/AIV 数量。

## 只保存最后一次采集

先更新使用仓的 AKL 子模块并重编译开启打点的版本，再在**所有 rank 启动前**设置：

```bash
git submodule update --init --recursive
# 沿用原芯片、top-k、RDMA 参数；下面是 A5 / 32 位索引示例。
DEBUG_CLOCK_ON=ON EP_NUM_TOPK_IDX_BITS=32 bash scripts/build.sh -soc_type Ascend950
export DISPATCH_CLOCK_DIR="$PWD/results/clock/exp01"
export AKL_TRACE_KEEP_LAST=1
# 原有分布式业务启动命令；把两个环境变量传给各机器、各 rank。
```

同一输出目录中，每个 rank/PID 滚动保留编号最大的完整采集：新文件全部关闭后才删除
上一次的 trace.bin/capture.json；其他 rank、其他进程和业务文件不动。损坏头或写入失败
保留旧的完整采集；并发逆序完成不会让旧编号覆盖新编号。默认不设置或设为 `0` 保留全部。
开关只控制新启动进程的导出，不会扫描删除以前进程留下的文件。每组实验使用新目录。
仍会逐次同步拷回和写入；此开关节省磁盘，不消除 kernel 打点或 D2H 的耗时。

已有 20 轮数据只想看最后一轮时，无需重新运行 kernel：

```bash
PYTHONPATH="$AKL_ROOT/python" python3 -m akl.semantic \
  /absolute/path/exp01 --source "$PWD/kernels/elastic_dispatch.cpp" \
  --jobs 64 --last-launch
```

每个实验目录选**编号最大的 launch**，保留其中所有 rank，不读取旧轮次的二进制。
若最后一轮缺少此前出现过的 rank，会报错并保留输入。只按已发现的 rank 检查，不猜测
预期 world size；跨进程编号一致需由业务调用顺序保证，多 PID/重启实验应分目录。
运行时已经只留最后一轮时，可省略 `--last-launch`。

## 最终只留一套报告

```text
exp01/result/
├── index.html                         # launch 选择入口
├── summary.json                       # 索引与采集状态
└── launches/launch19/                  # 每轮独立，不合并 20 轮时钟
    ├── index.html / summary.json      # rank 汇总与单次时间线切换
    ├── trace.json                     # 该轮所有 rank 的 Chrome/Perfetto JSON
    └── runs/rankN-pidP-launch19/
        ├── semantic.html
        ├── semantic.svg
        └── trace.json                 # 当前 rank，保留绝对 cycle 与命中序号
```

不再在输入 rank 目录保留第二套报告，不再默认生成 JSONL、counts.json 或任何 ZIP；
不存在总 result.zip。HTML/SVG/Chrome JSON、搜索、缩放和 block 筛选继续可用。
需要下载一个 launch 时，可加 `--zip-launches` 生成该轮独立 ZIP；仍不生成总 ZIP。
整个 result/ 可直接复制到本地打开，网页不依赖 Python 服务。

**默认全部解析成功且报告替换成功后，删除原始 trace.bin、capture.json 及旧派生副本。**
`--last-launch` 成功后也清理被跳过的旧轮次。仅删除已识别采集的固定文件名，不递归删业务目录；
有失败时整批原始数据保留，返回非零；报告发布失败时旧报告及原始数据均保留。
已有其他输出目录的旧报告不自动清理；本次 --output 指定的旧报告会被新结果整体替换。

后续还想改源码映射、重新生成报告或保留诊断原始数据时，**第一次解析就加
`--keep-intermediates`**，保留输入及每 rank 的 JSONL/counts。默认清理后不能再从二进制重解析；
直接查看最终 HTML/SVG/JSON 即可。单次 rank 目录解析也采用相同清理规则。

复现 CPU 合成性能对照：把基线和当前版本的 python/ 分别放到临时目录的 old/python/、new/python/，
执行 `python3 tests/benchmark_batch.py /absolute/path/临时目录`。脚本保存日志和 metrics.json，
每组统计完成后删除它自己生成的合成数据，以免复现实验再次留下数 GB 文件。
