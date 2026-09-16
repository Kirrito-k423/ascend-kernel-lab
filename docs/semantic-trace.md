# 语义打点与循环计数

本接口已通过 CPU 协议测试、浏览器检查，以及 CANN 9.1.0-beta.1/Bisheng 的 A3、A5 编译回归。使用仓 PR #20 已通过 A5 kernel 库编译；尚未执行该业务的 NPU 正确性或性能测试。既有 A3 micro-benchmark 结果不作为新接口的实测证据。

## 写法

```cpp
#include "akl/trace/semantic.h"
akl::Recorder<true, 256> clock;
#define DebugClock(...) AKL_DEBUG_CLOCK(clock, __VA_ARGS__)
DebugClock("big func", "sub func", "A part", "entry");
while (condition) {
    DebugClock("big func", "sub func", "A part", "iteration");
    // 原业务逻辑
}
DebugClock("big func", "sub func", "A part", "a part end");
```

- 每项是非空 UTF-8 字符串字面量，最后一项表示当前打点，其余项是父级。不需注册数字，不受其他位置插入打点影响。
- FNV-1a 在编译期生成内部 ID，离线扫描同一份源码还原路径；发现哈希冲突或未知 ID 就拒绝分析。建议将构建源码与结果一起留存。
- 同一路径每次执行都会追加记录；`sequence` 是核内顺序，`occurrence` 是该核该点第几次；`counts.json` 自动汇总每核每路径次数。
- 容量为每核记录条数，须为正偶数；例中 256 个槽位。满后保留前缀并累计 `dropped`。次数只覆盖保留记录，不能当成完整循环总次数；应增加容量后重跑。
- 打点只读 `GetSystemCycle`，没有核内 printf 或隐式 barrier。`entry/end` 等名字不自动产生同步或 span；图中区间表示一次打点到下一次打点。
- 字面量使用 JSON 兼容转义；不支持变量、相邻字面量拼接、raw string、八进制/十六进制转义或自定义打点宏名。直接宏首参须为记录器标识符。

## Host 与设备连接

Host 使用 `akl::Capture<256> trace(blocks, stream)`，将 `trace.Data()` 作为独立 GM 参数传入 kernel。每次 launch 都使用新 Capture，不能与业务 workspace 共用。

设备完成业务后，为 Flush 准备 `(8 + 2 * 256) * sizeof(uint64_t)` 字节专属 UB，调用 `clock.Flush(trace_output, scratch, 0)`。最后一个参数是可选的业务保留数量；0 表示本接入不使用该字段。

Flush 的 EVENT_ID0（S→MTE3、MTE3→S）必须空闲；仅支持每个逻辑 block 独占一个 AIV 的映射。Host 调用 `trace.Export(root, rank)` 同步所属流、复制原始 uint64 数据，写入独立 launch 目录。关闭时不构造 Capture，使用 `Recorder<false, 256>` 或空宏。

当前仅支持普通 launch；图捕获、跨卡校准、混合 AIC/AIV 和生产并发集成尚未验收。增加容量也会增加设备局部存储和导出 UB 开销，须在目标芯片检查资源与扰动。

## 构建依赖与离线分析

设备和 Host 部分均为 header-only；使用仓固定本仓提交，将 `include/` 加入自己的构建路径。无需构建本仓的 micro-benchmark，通用库不携带业务仓补丁。

`ascend_deepep` 的构建依赖、独立 GM 参数和打点替换见 [使用仓 PR #20](https://gitcode.com/ChenDonYY/ascend_deepep/merge_requests/20)；业务编译/运行步骤以该 PR 说明为准。

每次 `Capture::Export` 生成独立 `rankN-pidP-launchL/trace.bin` 和 `capture.json`。将下面的路径换成实际采集目录与参与构建的打点源码：

```bash
export AKL_ROOT=/path/to/ascend-kernel-lab
PYTHONPATH="$AKL_ROOT/python" python3 -m akl.semantic \
  /path/to/results/rankN-pidP-launchL \
  --source /path/to/kernel.cpp
```

生成 `semantic.html`、`semantic.svg`、`semantic.jsonl`、`counts.json`。离线入口只依赖 Python 标准库；不同 launch 的原始数据不覆盖，分析时使用与构建一致的源码。

## 阅读与验证

HTML 自包含；色带自上而下对应路径层级，连续父路径合并显示，叶级保留每次命中。可调整显示层数，隐藏层级后会同步压缩 block 高度；默认四级 block 间距为 72，比原版 118 减少约 39%。悬停显示路径、顺序范围、原始 cycle 与差值。表格保留每一次 cycle 和出现次数，默认折叠。

HTML 顶部时间刻度在图内滚动时保持可见，鼠标对齐线贯穿所有 block；单击固定，再次单击解除。读数同时显示相对 Δcycle 与绝对 cycle，鼠标位置标为插值估计。SVG 每 8 个 block 重复刻度，并使用共同纵向网格。

默认仅显示 cycle。确认 cycle 时钟频率后，可给离线命令增加 `--clock-mhz <实际MHz>`，在同一轴上增加 µs 刻度和悬停耗时；换算公式为 `µs = Δcycle / MHz`，不会修改原始记录或自动校准时钟。例如 `--clock-mhz 1000` 仅适用于已确认频率为 1000 MHz 的情况，不是默认硬件频率。

升级分析脚本后，可直接重新处理已有 `trace.bin`/`capture.json`，不需要重新编译 kernel 或重新采集。

原始 cycle 是 uint64/十进制字符串，不经浮点存储；绘图先用 Python 整数减共同 origin，再缩放。未确认频率时只显示 cycle，不硬编码时间换算；跨核对齐仍标为 unverified。

应用关联测试 PR 后，在本仓运行：

```bash
python3 -m unittest discover -s tests -v
mkdir -p results/semantic-cpu
clang++ -std=c++17 -O2 -Wall -Wextra -Werror -Iinclude -Itests/cpu_stubs \
  tests/semantic_cpu.cpp -o results/semantic-cpu/check
results/semantic-cpu/check results/semantic-cpu/captures
```

C++ 检查使用明确的 CPU API 替身，覆盖真实 Recorder/Capture 的数据协议、循环次数、溢出和关闭路径；不模拟 NPU 流水、时钟域或性能。Python 测试覆盖大整数精度、解析、损坏记录、转义和 HTML 输出。

真实 CANN 编译回归（应用 CPU/CANN 检查 PR 后）：

```bash
source /usr/local/Ascend/cann/set_env.sh
bisheng -xasc --npu-arch=dav-3510 -std=c++17 -O2 -Iinclude \
  -c tests/semantic_cann.cpp -o /tmp/semantic_cann.o
```

`dav-3510` 为 A5；通用接口 A3 检查使用 `dav-2201`。用例保留真实 GM 字符串类型，覆盖设备 constexpr 哈希、循环打点、关闭路径和 Flush。编译成功不等于 NPU 执行；使用仓当前 SIMT 分支的完整 A3 构建仍受架构限制。
