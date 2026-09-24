# 950：采样重放会不会改变应用看到的状态

上一轮已验证合成 VF 的源码/指令/停顿样本关联；这一轮检验 profiler 是否保留应用可见的本地 GM 状态。
每个 SIMT 线程在索引表末尾独占一个 uint32 计数器，每次执行加 1，Host 读回全部 32 个计数并校验原有结果。
它不访问 SQ/CQ、doorbell 或远端；即使通过，也不能证明真实 `elastic_dispatch` 可以安全重放。

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

| 对照 | 应用显式启动 | 状态要求 |
| --- | --- | --- |
| 普通一次 | 1 | 32 个计数全为 1，原有 32 项结果校验通过 |
| 普通两次 | 2 | 32 个计数全为 2，证明计数器可观察累加 |
| kernel / application 采样 | 每次应用内 1 | 每个完整应用的 32 个计数全为 1，同时保留 profiler 日志 |

`AKL_APP_START` 标记应用进程开始，`AKL_STATE` 输出 PID、期望值和全部计数；`PASS` 在两类校验通过后输出。
manifest 的 `state_observations` 保存各阶段记录；状态异常、缺失记录或应用未完整结束都判失败，即使已有报告。
计数器可能被 profiler 的内存快照恢复，因此“计数为 1”不等于“只执行一次”；不能据此推算真实启动次数。
采样成功仍标为 `captured_pending_review`，需检查新报告的源码映射与样本；停顿样本不能换算逐行微秒。

## 3. 回传与后续判断

回传 `results/simt-state-*.tar.gz` 四个包（失败包也保留），包含环境、源码、二进制、状态记录和采集日志。
上传前检查机器路径/设备信息是否适合公开；不把原始包加入 Git。无需再采旧 retry1 探针。
任一普通对照失败：修复计数器或同步后再测；仅采样失败：固定实际工具版本，定位额外执行及状态恢复边界。
全部通过：只说明这个单卡合成例子的本地状态一致，下一步仍需真实启动命令、rank/卡数、源码提交及通信状态恢复证据。
本轮本地只完成 CPU 模型的地址/未定义行为检查和采集流程测试；新版本的 CANN 编译、950 执行与多 rank 验证均待回传。

固定参考：[20 次预热实现](https://github.com/Ascend/msopcom/blob/002ae2823b417d50b9786f10b2fb104677626e24/csrc/runtime/inject_helpers/ProfDataCollect.cpp#L1287-L1330)、[重放模式约束](https://github.com/Ascend/msopprof/blob/7e847da532c7fdfafa32af98bedd823a13e1ea23/docs/zh/user_guide/msopprof_user_guide.md)。
安装版本以每个包的 profiler/compiler/driver 日志为准；application 的单卡成功不能推广到官方不支持的多卡多算子场景。
