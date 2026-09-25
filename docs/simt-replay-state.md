# 950：采样重放会不会改变应用看到的状态

本轮已在 Ascend950DT_9582 验证：kernel 重放改变应用可见的本地 GM 状态；application 对照的五次应用运行通过。
每个 SIMT 线程在索引表末尾独占一个 uint32 计数器，每次执行加 1，Host 读回全部 32 个计数并校验原有结果。
它不访问 SQ/CQ、doorbell 或远端；即使通过，也不能证明真实 `elastic_dispatch` 可以安全重放。
证据来自 [`82e4970` 的四份回传](https://github.com/Kirrito-k423/ascend-kernel-lab/pull/43#issuecomment-5824859470)，源码和二进制哈希已核对。无需重跑，以下命令保留用于复现。

## 1. 取完整实验分支

在干净的实验 checkout 中执行（保留其他工作目录的改动）。先使用完整分支回测；合 PR 时按依赖从底到顶，每个后续 PR 先改目标为 master 再合，避免内容留在已合并的旧分支。

```bash
git fetch origin codex/simt-replay-guide
git switch --detach origin/codex/simt-replay-guide
source /实际安装目录/set_env.sh
npu-smi info
SIMT_DEVICE=0  # 改为已确认空闲的 ACL 逻辑设备号
```

## 2. 逐项执行，保留四个独立回传包

先校准普通运行；两项必须都出现 `baseline_passed`，否则停止并回传：

```bash
python3 scripts/run_simt_probe.py --device "$SIMT_DEVICE" --stateful --output results/simt-state-once
python3 scripts/run_simt_probe.py --device "$SIMT_DEVICE" --stateful --host-launches 2 --output results/simt-state-twice
```

确认校准通过，再分别执行以下命令；不要并发启动：

```bash
python3 scripts/run_simt_probe.py --device "$SIMT_DEVICE" --stateful --profile --replay-mode kernel --output results/simt-state-kernel
```

```bash
python3 scripts/run_simt_probe.py --device "$SIMT_DEVICE" --stateful --profile --replay-mode application --output results/simt-state-application
```

若 kernel 模式只报 GM 状态不匹配且已正常退出，可继续 application 对照；超时或设备错误则先停止并回传。
每条命令默认超时 180 秒，仅清理它启动的进程组，不自动重试。输出目录必须全新；复测另换后缀。

| 对照 | 应用显式启动 | 真实回传结果（每次检查 32 个计数） |
| --- | --- | --- |
| 普通一次 | 1 | 全为 1，原有结果校验通过 |
| 普通两次 | 2 | 全为 2，原有结果校验通过 |
| kernel 采样 | 1 | 全为 24，应用校验失败；profiler 返回 0 且产生报告 |
| application 采样 | 每次应用内 1 | 5 个不同 PID，计数每次全为 1，原有结果校验均通过 |

`AKL_APP_START` 标记应用进程开始，`AKL_STATE` 输出 PID、期望值和全部计数；`PASS` 在两类校验通过后输出。
manifest 的 `state_observations` 保存各阶段记录；状态异常、缺失记录或应用未完整结束都判失败，即使已有报告。
计数器可能被 profiler 的内存快照恢复，因此“计数为 1”不等于“只执行一次”；不能据此推算真实启动次数。
采样成功仍标为 `captured_pending_review`，需检查新报告的源码映射与样本；停顿样本不能换算逐行微秒。
本次 application 报告已离线核对源码、逐 PC 原始计数及逐行汇总：10,037 个原始计数中 9,999 已映射，38 未映射；目标 VF 为 9,944，其他已映射指令为 55。
第 15 行校验和关联 6,757 个计数，占全体已映射计数的 67.58%；不是该行耗时比例。Insight 界面和真实通信仍未验收。

## 3. 回传与后续判断

后续回传 `results/simt-state-*.zip`（失败包也保留），包含环境、源码、二进制、状态记录和采集日志；本次旧 tar.gz 已完整读取。
ZIP 必须小于 5,000,000 字节；超限则拒绝生成回传包并记录 `archive_error`，完整结果仍留在原目录，不静默删减证据。
上传前检查机器路径/设备信息是否适合公开；不把原始包加入 Git。无需再采旧 retry1 探针。
任一普通对照失败：修复计数器或同步后再测；仅采样失败：固定实际工具版本，定位额外执行及状态恢复边界。
当前结论：默认 kernel 重放不能直接用于真实通信；application 的本地状态通过不解除多卡限制，下一步需真实启动命令、rank/卡数、源码提交及通信状态恢复证据。
设备结果对应 `82e4970`；后续改动仅涉及 ZIP 回传与说明。本地 5 个采集测试方法通过，四份真实回传的 ZIP 重打包均小于 5 MB；未新增设备运行。

固定参考：[20 次预热实现](https://github.com/Ascend/msopcom/blob/002ae2823b417d50b9786f10b2fb104677626e24/csrc/runtime/inject_helpers/ProfDataCollect.cpp#L1287-L1330)、[重放模式约束](https://github.com/Ascend/msopprof/blob/7e847da532c7fdfafa32af98bedd823a13e1ea23/docs/zh/user_guide/msopprof_user_guide.md)。
安装版本以每个包的 profiler/compiler/driver 日志为准；application 的单卡成功不能推广到官方不支持的多卡多算子场景。
