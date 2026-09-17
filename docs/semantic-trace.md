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

同步导出使用 ACL 页锁定主机内存拷回。有效记录仍逐次保存；仅去掉所有 block 都未使用的尾部槽位，文件 `capacity` 是紧凑后的正偶数行容量，新增 `recorder_capacity` 保留配置容量。不会减少记录或改写 cycle/count/dropped；旧 v1 解析器仍可读取。缺失/损坏行保留原容量，供离线检查报错。`Export()` 成功返回时文件已关闭、可立即读取，不使用后台写入线程。

此次修改位于 Host 头文件，使用仓须更新子模块并重新编译；重新出图本身不能加速旧二进制。填满容量时无法缩短文件，实际收益取决于记录量与文件系统。

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

## 多 rank、多次执行：父目录批量分析

采集完成后直接传入 `DISPATCH_CLOCK_DIR`，无需循环拼接每个 launch 路径：

```bash
PYTHONPATH="$AKL_ROOT/python" python3 -m akl.semantic \
  "$DISPATCH_CLOCK_DIR" --source /path/to/kernel.cpp
# 可加 --output /path/to/experiment-result，默认输出到 <父目录>/result。
```

递归识别 `rank*-pid*-launch*`，每个有效采集目录仍生成 HTML/SVG/JSONL/counts；另生成 `result/index.html`，按 rank 筛选、在汇总统计和单次时间线间切换，并按 PID/launch 选择采集。汇总包括每 rank 成功/失败数、丢弃告警、事件数、核内首末跨度，以及各语义点的命中次数和到下一点的 cycle 差值 min/mean/max。末点没有后继，不纳入区间耗时；均值按有效区间数加权。跨 rank 时钟不对齐，进程内 launch 编号不自动视为跨 rank 的实验轮次。

`result/result.zip` 可单独下载，解压后打开 `result/index.html`；也可完整拷走 result 目录。包内包含各次原始 trace.bin/capture.json、HTML/SVG/JSONL/counts、全部事件 events.jsonl、汇总 summary.json、事件映射和源码快照。页面无需网络或本地服务器，只加载选中的时间线；解压包不再嵌套一份自身 ZIP。

批量处理按相对路径区分不同实验子目录中的同名 launch；重复分析不会扫描既有汇总包。损坏/缺失采集被记录为失败，并保留可复制的原始文件；其他采集继续导出，有失败时最终退出码为 1。报告只统计实际发现的采集，不猜测缺失的 rank 或轮次。已有 result 只有被识别为本工具报告时才整体替换，其他目录会拒绝覆盖。

`--clock-mhz` 和 `--cycle-range` 对每个采集分别生效；若指定窗口超出某次跨度，该采集会标记失败。批量同样要求全部采集使用与 `--source` 一致的打点源码。

## 阅读与验证

HTML 自包含；色带自上而下对应路径层级，连续父路径合并显示，叶级保留每次命中。相邻区间采用多色大幅跳色；颜色不再表示父子关系，层级由纵向位置与标签表示。可调整显示层数，隐藏层级后会同步压缩 block 高度；默认四级 block 间距为 72，比原版 118 减少约 39%。悬停显示路径、顺序范围、原始 cycle 与差值。表格保留每一次 cycle 和出现次数，默认折叠。

HTML 顶部时间刻度在图内滚动时保持可见，鼠标对齐线贯穿所有 block；单击固定，再次单击解除。读数同时显示相对 Δcycle 与绝对 cycle，鼠标位置标为插值估计。SVG 每 8 个 block 重复刻度，并使用共同纵向网格。

横向缩放仅改变时间窗口：Ctrl/⌘＋滚轮以鼠标位置为中心缩放，拖拽框选放大；Shift＋拖拽或滚轮平移，也可使用放大、缩小、左右平移、全范围按钮。起点/终点输入框支持精确到 1 cycle 的窗口，值为相对共同 origin 的 Δcycle。缩放后完整语义标签会重新展开；层级折叠与悬浮刻度继续生效。

短段最小显示 1px 标记并优先绘制，避免被相邻长段遮住；标记宽度不代表真实耗时，真实值见悬停读数。很多短段挤在同一像素时，仍需放大或输入精确范围逐项查看。静态 SVG 可使用同一窗口参数单独导出，例如：

```bash
PYTHONPATH="$AKL_ROOT/python" python3 -m akl.semantic \
  /path/to/results/rankN-pidP-launchL --source /path/to/kernel.cpp \
  --cycle-range 1200 1201
```

`--cycle-range START END` 同时设置 HTML 初始窗口与 SVG 导出范围，要求 `0 ≤ START < END ≤ 总跨度`；省略时显示全范围。HTML 内仍可恢复全范围，原始 JSONL 与次数统计保持完整。重新分析会覆盖该采集目录的派生图表。

HTML 可以编辑 `1 cycle = … µs`，默认 `0.001 µs`；坐标轴下拉菜单在 cycle/µs 间切换，刻度、鼠标读数和悬停耗时随换算更新。若传入 `--clock-mhz <实际MHz>`，HTML 初始换算为 `1 / MHz`。默认换算是显示设置，不代表测得或校准的芯片时钟；原始绝对 cycle 和横向 cycle 窗口不变。静态 SVG 仍仅在指定 `--clock-mhz` 时增加 µs 刻度。

为减少 64 block 的浏览器负担，HTML 默认只绘制前 8 个 block。Block 输入框支持 `0-7,16,32-39`，点击“显示所选 block”生效，也可一键显示全部；编号保留原值，选中的行紧凑排列。未选中的 block 不创建图形节点；统计和原始记录表仅在展开时创建所选 block 的行，收起时释放。全部数据仍保留在 HTML 数据区与 JSONL/counts 中；独立 SVG 导出也保持完整 block 范围。

深浅由实际区间长度决定：短段更深，长段更浅，所有 block 共用当前采集的核内最长跨度作为尺度，缩放、单位及 block 筛选不改变颜色。背景按 sRGB 亮度调节，配深色文字；代表性配色回归的文字对比度超过 5:1。零时长末点仍只是最小宽度标记。

“搜索模块”按完整语义路径进行不区分大小写的字面量子串匹配；搜索父模块会同时匹配其子阶段。仅高亮当前时间窗口、所选 block 和可见层级里的匹配区间，描边并淡化其他区间，显示可见匹配数。清除搜索恢复原配色，原始数据与静态 SVG 不受影响。

升级分析脚本后，可直接重新处理已有 `trace.bin`/`capture.json`，不需要重新编译 kernel 或重新采集。

原始 cycle 是 uint64/十进制字符串，不经浮点存储；绘图先用 Python 整数减共同 origin，再缩放。µs 按用户配置换算，原始 cycle 不变；跨核对齐仍标为 unverified。

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

## Chrome Trace JSON

相同分析命令还生成每个采集目录的 `trace.json`，父目录模式额外生成 `result/trace.json`，包含所有有效 rank/launch；失败采集记录诊断事件。完整 ZIP 包含这些文件，可从报告直接下载。
把单个 JSON 导入 Chrome tracing 或 Perfetto（Open trace file）。每次采集为独立进程轨道组，block/subblock 为线程轨道；父语义合并连续区间，叶子保留循环命中，末点与零时长点使用 instant 事件。
`ts`/`dur` 的单位是 µs；默认 1 cycle = 0.001 µs，`--clock-mhz MHz` 改为 1/MHz。默认值仅为显示换算；HTML 内修改单位/比例不改已导出的 JSON，需带参数重新分析。
原始绝对 tick、区间末 tick、次数与 sequence 保存在事件属性中；大整数 cycle 用字符串保存。每次采集各自减共同起点，不表示跨核、跨 rank 或跨 launch 时钟已校准；不能从汇总轨道重叠推断并发关系。
JSON 保留全部 block 和完整区间，不受 HTML 筛选或 `--cycle-range` 裁剪；批量逐次追加，不将全部采集同时加载到内存。此功能仅需更新 Python 工具并重新出图，无需重新编译或采集。
