# ascend-kernel-lab

面向 Ascend 通算融合算子的独立调试与接口实验仓库。把多核行为转化为可核对的时间线，把接口疑问转化为可复现的 micro-benchmark，持续积累带适用条件的结论。

## 当前状态

**已完成 A3 单卡最小实机闭环。** DataCopy 支持1/8/48个AIV的连续与跨步读写，GatherMask支持固定及自定义掩码的正确性、计时和出图。106个配置、2998次启动通过验证（含预热及采集开关对照）。提供原始tick、CSV、PNG/SVG、交互HTML和Trace JSON。

先看 [运行与接入说明](docs/quickstart.md)、[实测报告](reports/a3-20260915/README.md) 和 [实现边界](docs/implementation-status.md)。跨核时钟严格校准、多卡竞争和真实业务仓接入尚未完成。

新增 [A5 对齐与 SIMT 实测](examples/a5_mbench/README.md)：CANN 9.1.0、单 AIV、本地 GM。两轮共 4640 个配置轮次、60264 个计时样本，通过输出、原始计时、构建和占用证据校验；[交互网站](https://kirrito-k423.github.io/micro-benchmark-lab-web/?lab=alignment) 提供曲线、表格、原始样本与 PNG/SVG。

新增 [A5 SIMD / REG 同工作量对照](examples/a5_simd_mbench/README.md)：15 个 shape，Tensor API、REG 1/4 组与 SIMT 五档线程；1588 配置 × 两轮、47640 计时样本，完整输出和边界验证通过。[交互曲线](https://kirrito-k423.github.io/micro-benchmark-lab-web/?lab=simd) 同时保留一次批量与长依赖链、空对照和原始样本。

| 能力 | 目标 | 入口 |
| --- | --- | --- |
| Kernel Trace | 独立打点、原始时间戳、核间起点差异、阶段耗时、图片与时间线 | [设计](docs/trace-design.md)、[skill](skills/ascend-kernel-trace/SKILL.md) |
| API Micro-benchmark | 接口语义、参数边界、延迟、吞吐及竞争实验 | [设计](docs/benchmark-design.md)、[skill](skills/ascend-micro-benchmark/SKILL.md) |

## 从这里开始

1. 阅读 [需求与验收标准](docs/requirements.md) 和 [DebugClock 调查](docs/debugclock-review.md)。
2. 在目标环境填写 [实验记录](templates/experiment.md)，确认芯片、CANN、工具链与拓扑。
3. 调用 `$ascend-kernel-trace` 调试真实算子，或 `$ascend-micro-benchmark` 验证接口。两个 skill 均要求区分已设计、已实现和已验证。
4. 按 [运行说明](docs/quickstart.md) 复现已验证实验，再依据 [实施顺序](docs/roadmap.md) 扩展。

## Skill 安装

规范源目录为 `skills/ascend-kernel-trace/` 和 `skills/ascend-micro-benchmark/`，保持在 Git 仓库中。全局安装只创建指向源目录的绝对路径符号链接。首次安装前检查同名入口，保留已有内容。链接到用户的 `~/.codex/skills/` 后，重新加载或新建任务使其可发现。

## 原则

- 原始 tick 与耗时分别存储；未经验证的时钟域不合并。
- 首个打点是可观测入口，不默认等于硬件精确启动时刻。
- 异步 API 的发射耗时与完成耗时分别测量。
- 512B 是实验变量，cache line 大小绑定具体芯片及依据。
- 保留失败、缺失和扰动；没有实测数据就不生成性能结论。
- 目标仓只做显式接入；通用采集、解析、绘图和知识由本仓维护。

记录器和实验实现均独立编写，未复制参考仓库实现。参考来源和检查版本见调查记录。
