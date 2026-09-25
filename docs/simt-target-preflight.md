# 下一步：核查真实 LoadSqCqContexts 的构建产物

目标是确认真实优化构建能否提供设备 ELF、目标函数信息和源码行映射。上一轮 kernel 重放改变了本地状态，因此本轮只读文件，不加载业务共享库、不启动 NPU 或 profiler。
需要 A5 上现有的构建产物和 CANN LLVM 工具；通常几分钟，每条工具默认最多 120 秒，不要求占用空闲卡。

## 1. 取得完整实验分支

在干净的 ascend-kernel-lab checkout 中执行：

```bash
git fetch origin codex/simt-target-preflight-checks
git switch --detach origin/codex/simt-target-preflight-checks
source /实际安装目录/set_env.sh
DEEPEP=/实际路径/ascend_deepep
find "$DEEPEP/build" -type f -name '*elastic_dispatch*.o'
```

选择当前业务构建的对象文件。以下是已核对目标仓 CMake 结构的默认路径；如 build 目录不同，替换为上一步找到的实际路径。
先检查现有优化构建，不为此改优化级别或触发业务运行；如果没有调试信息，回传后再决定如何增加 `-g`。

```bash
TARGET_OBJECT="$DEEPEP/build/CMakeFiles/ascend_deepep_kernels.dir/kernels/elastic_dispatch.cpp.o"
python3 scripts/collect_simt_target.py --repo "$DEEPEP" --object "$TARGET_OBJECT" \
  --output results/simt-target-first
```

脚本优先从 CANN 目录寻找 `llvm-objdump` 和 `llvm-dwarfdump`；必要时用 `--tool-dir /实际LLVM目录` 指定。
也可提供 `.so`，但它可能拼接多个设备镜像；发生边界检查失败时回传即可，优先使用单个翻译单元的 `.o`。
已有独立设备 ELF 时使用 `--device-elf --object /实际/device.o`；Host ELF 会被拒绝。

## 2. 补充真实运行上下文

将当前能正常运行的启动命令、rank/卡数、shape、目标芯片、构建命令和优化选项写入一个文本文件，再加 `--context /实际/run-context.txt`。
此文件只归档，不执行其中内容；请去掉密码或令牌。暂时没有上下文时仍可先运行上面的只读检查。
默认读取 `$DEEPEP/build/compile_commands.json`，仅归档对应源码的条目；其他路径使用 `--compile-commands /实际/compile_commands.json`。
如果数据库不存在，manifest 的 `compile_entries=0`，不根据默认值猜测实际编译参数。

## 3. 回传与验收

回传 `results/simt-target-first.zip`，失败包也保留；每次使用新输出目录，不自动重试或改动业务文件。
ZIP 包含 manifest、目标源码快照、提取的 device.o、工具版本、函数 DWARF、行表、符号和反汇编日志。
包内仍沿用 `simt_probe/` 根目录；通过 `schema=akl.simt_target.v1` 区分本轮只读证据。
每个 ZIP 严格小于 5,000,000 字节；若完整包超限，原目录保留，先生成仅含状态的反馈包：

```bash
python3 -m zipfile -c results/simt-target-status.zip results/simt-target-first/manifest.json
```

| 结果 | 含义与下一步 |
| --- | --- |
| `collected_pending_review` | 收集命令成功；需人工核对目标 VF 的设备 PC 范围与源码行，不能当作映射已通过 |
| `inspection_incomplete` | 有工具缺失或报错，日志已保留；按实际 CANN 工具能力修正 |
| `failed` | 对象路径、格式或边界不符；先修复输入，不启动通信采样 |

函数名称命中、`.debug_line` 存在、`Attr_Section_Lcal` 节存在都只是线索；MC2/LCCL 运行时分类及通信重放安全性仍为未知。
文件哈希固定了本次证据，但不能单独证明源码与对象来自同一次编译；还需核对构建上下文和 DWARF 路径。
成功标准是能建立目标 VF 的设备指令/源码关联；否则停在编译或映射问题。即使满足，也仍需验证多 rank 协调采集方案。
本地已用真实 950 回传对象核对设备 ELF 提取与运行时 dump 完全一致；Mac LLVM 不支持其反汇编，已正确保留不完整状态。本轮未新增 A5 执行。

依据：[目标仓固定 CMake 配置](https://gitcode.com/ChenDonYY/ascend_deepep/blob/b0ba7f7ee1a2665b32c9328c586de97d7b2f5169/CMakeLists.txt)、[LLVM DWARF 检索与行表](https://llvm.org/docs/CommandGuide/llvm-dwarfdump.html)。
