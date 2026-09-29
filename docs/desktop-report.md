# 用 AKL Report 查看实验数据

## 查看者：下载、拖入、打开报告

1. 在桌面应用 PR 的 **Checks → Desktop app → Artifacts** 下载对应系统包。macOS 包适用于 Apple Silicon / macOS 14 及以上；Windows 包适用于 Windows 10/11 x64。
2. **完整解压**。macOS 打开 `AKL Report.app`；Windows 打开 `AKL-Report.exe`，保留旁边的 `_internal` 文件夹。
3. 把实验 ZIP 拖进窗口，也可点击「选择实验包 / CSV」。无需安装 Python、AKL、CANN，不需要找原来的 C++。
4. 确认输出目录，点击「生成报告」。默认为 ZIP 旁的新目录；已有结果不会被覆盖。
5. 点击「打开 HTML 报告」查看，或把**整个结果目录**复制给同事。

应用完全在本机解析，不上传实验数据。当前构建未做 Apple 公证和 Windows 代码签名；受组织策略限制的电脑需要使用组织认可的签名构建。

### 会得到什么

| 输入 | 输出 |
| --- | --- |
| Trace | 按 launch/rank 分组的 HTML、SVG、Chrome Trace JSON；每个 rank 的耗时柱图、饼图 PNG；HTML 可切换各个 core |
| Latency CSV | 延迟图、每轮最慢/平均/最快曲线图，以及 CSV 带有数据量时的独立带宽图；PNG、SVG 和统计 JSON |

入口是 `index.html`。输入 ZIP 始终保留，不重复产生总结果 ZIP。默认最多 4 个解析进程；内存不足可调到 1–2，有充足内存可增大，最高 64。

Trace 默认仅以 **1 cycle = 0.001 µs** 作显示换算，可在应用或时间线中调整；这不是自动识别的设备频率。Latency CSV 使用文件里已经记录的 `elapsed_us`，不受此选项影响。跨核、跨 rank 时钟没有因此获得校准。

## 采集者：一次打包，查看者不需要源码

DeepEP 接入对应 PR 后，在原多机运行命令前设置：

```bash
export AKL_REPORT_MODE=desktop
export DISPATCH_CLOCK_DIR=/shared/experiments/run-001/clock
export DISPATCH_RESULTS_DIR=/shared/experiments/run-001/latency
# 保持 AKL_AUTO_PARSE=1（默认）；按原来的命令运行多机脚本。
```

全部节点成功后，主节点会统一生成 `$DISPATCH_CLOCK_DIR/experiment.zip`，无需服务器绘图。每组实验使用独立目录；原始数据保留，已有 ZIP 不覆盖。`scripts/build.sh` 会将事件名称映射随 kernel 构建保存到 `ascend_deepep/lib/event_map.json`。

对于其他算子或历史数据，在采集完成、原始文件尚未被解析清理时执行：

```bash
PYTHONPATH="$AKL_ROOT/python" python3 -m akl.report_bundle pack "$CLOCK_DIR" \
  --source kernels/your_kernel.cpp \
  --latency "$RESULTS_DIR/dispatch_latency.csv" --output experiment.zip
```

纯 trace 可省略 `--latency`。已有构建时映射时，用 `--event-map event_map.json` 替代 `--source`；不要用修改后的源码为旧实验补名称。纯 latency 可直接发送 CSV。

## 为什么旧命令需要 C++

`trace.bin` 为降低采集开销只保存事件 ID 和时间戳，C++ 原来用于把 ID 还原成文字标签。现在只需把这份小型映射随 ZIP 带上，无需传源码。旧 ZIP 没有映射也能解析，但模块名称显示 event ID；如果缺少 `capture.json`，请从原始采集补齐，它记录 rank、block 数和二进制布局，不能凭空推断。

应用接受 ZIP；不解压包内的程序。默认展开限制为 32 GiB / 100000 个文件，过大的实验请按组拆包。

## 开发者构建

在目标系统安装 Python 3.12 后运行 `python -m pip install -r desktop/requirements.txt`，设置 `PYTHONPATH=python`，再运行 `python -m PyInstaller --noconfirm desktop/AKL-Report.spec`。产物在 `dist/`。Windows 和 macOS 分别构建，不能用 Mac 构建 Windows 可执行文件。
