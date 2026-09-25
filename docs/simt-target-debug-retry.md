# PR45 后续：补充调试信息，核查实际解码能力

## 1. 本轮要验证什么

[PR45 回传](https://github.com/Kirrito-k423/ascend-kernel-lab/pull/45#issuecomment-5826801286) 已取得真实源码及按业务规则单独重编的设备 ELF。源码提交为 `230143f`，目标函数位于 1426–1506 行。
符号范围为 `[0x16398, 0x16c70)`，但目标 233 条指令记录全部是 `<not available>`，整个文件共有 25,326 条占位记录；退出码为 0 不等于可读汇编。
设备 ELF 没有 `.debug_line`；系统 LLVM 17 的 dwarfdump 还输出 `Error in creating MCRegInfo`。这两项均阻止当前映射验收。
本轮仅编译独立对象并读取文件；无需占用 NPU，不启动 profiler、业务入口或多 rank 通信。

## 2. 使用完整实验分支，先重查原对象

```bash
git fetch origin codex/simt-target-debug-retry
git switch --detach origin/codex/simt-target-debug-retry
source /实际CANN安装目录/set_env.sh
DEEPEP=/实际路径/ascend_deepep
BASE_OBJECT=/上轮保留的/elastic_dispatch.cpp.o
python3 scripts/collect_simt_target.py --repo "$DEEPEP" --object "$BASE_OBJECT" \
  --output results/simt-target-host-retry
```

现在允许普通同名节，仍拒绝多个 `.aicore_binary` 或拼接设备镜像。预期可以直接提取 Host 对象中的设备 ELF，但原工具的占位汇编会令结果变成 `inspection_incomplete`。
原对象未改动时，提取设备 SHA256 应为 `616058470fec17b12139288bd2f49520a924268bd9f50178bdc3e0a780128c9b`；不一致就回传，不套用旧地址范围。

## 3. 保留业务优化，只给独立对象增加 -g

上轮未回传完整展开的编译命令，`compile-command.json` 也是空数组，因此不能从附件拼造 include/宏定义。
在 A5 上复用上轮成功的完整单 TU 编译命令；若已丢失，从当前 `flags.make` 和 `build.make` 恢复。不要直接执行业务 make target。
将完整命令保存为 `compile-debug.sh`：保留编译器、工作目录、所有 include/宏、优化参数及顺序，只增加 `-g`，并把输出改到新临时目录。
尤其保留 TU 专属 `-DDEBUG_CLOCK_ON`、有效的 `-O2`、`-xasc`、`--npu-arch=dav-3510`、SIMT/SHMEM 参数；不切换 Debug/-O0 构建。
若有 `-MF` 依赖文件或其他输出参数，也重定向到临时目录；不链接、不替换业务 `.so`。检查脚本后再执行。

```bash
RETRY_DIR=$(mktemp -d /tmp/simt-target-debug.XXXXXX)
# 将完整单 TU 编译命令写入 "$RETRY_DIR/compile-debug.sh"；输出设为 "$RETRY_DIR/elastic_dispatch.cpp.o"。
# 脚本内必须使用实际绝对路径；同时保存原命令 compile-baseline.sh 以便核对差异。
bash -x "$RETRY_DIR/compile-debug.sh" > "$RETRY_DIR/compile-debug.log" 2>&1
```

成功后把实际工作目录、编译命令、编译输出及工具能力归档为上下文。以下工具只请求版本/帮助，不加载业务库：

```bash
{
  pwd
  cat "$RETRY_DIR/compile-baseline.sh" "$RETRY_DIR/compile-debug.sh" "$RETRY_DIR/compile-debug.log"
  "$ASCEND_HOME_PATH/tools/bisheng_compiler/bin/bisheng" --version
  "$ASCEND_HOME_PATH/tools/bisheng_compiler/bin/llvm-objdump" --help
  command -v llvm-dwarfdump
  llvm-dwarfdump --version
  find "$ASCEND_HOME_PATH" -type f \( -name llvm-objdump -o -name llvm-dwarfdump -o -name llvm-symbolizer \)
} > "$RETRY_DIR/run-context.txt" 2>&1
python3 scripts/collect_simt_target.py --repo "$DEEPEP" \
  --object "$RETRY_DIR/elastic_dispatch.cpp.o" --context "$RETRY_DIR/run-context.txt" \
  --output results/simt-target-debug
```

编译失败时停止收集新对象，将编译脚本和日志打成小于 5 MB 的 ZIP 回传。不要使用旧对象冒充此次输出。
`-g` 用于生成调试信息（[Clang 参数说明](https://clang.llvm.org/docs/CommandGuide/clang.html)）；是否在该 CANN 构建的设备 ELF 生效，以本轮产物为准。
若占位输出或 MCRegInfo 错误持续，回传本轮工具帮助/版本供匹配解码器；不要猜测 `--mcpu` 值或使用 profiler 重放来绕过工具问题。

## 4. 回传和判定

回传 `results/simt-target-host-retry.zip` 和 `results/simt-target-debug.zip`，每包严格小于 5 MB；失败 ZIP 同样保留。
超限时按[原 SOP](simt-target-preflight.md)发送 manifest 状态包，原目录保留。本轮结果不填写耗时柱状图。

| 检查 | 必须看到的证据 |
| --- | --- |
| 提取修复 | 普通重复节可接受，设备哈希与原独立提取结果一致 |
| 编译来源 | 完整命令、源码/对象哈希、工作目录，确认不是部署 `.so` 的运行采样 |
| 源码行表 | `.debug_line` 存在且解析出目标源码文件与目标 VF 地址范围中的有效行号 |
| 汇编解码 | 目标范围输出真实指令，不能把 `<not available>` 当作汇编；警告须复核 |

优化后可能多行对应一条指令，或一行对应多个 PC 区间；加 `-g` 后地址也可能变化，重新核对，不能沿用旧地址。
只有源码/汇编关联闭合后，才设计多 rank 协调采样。已有 kernel 重放会改变本地状态，application 重放也未证明真实通信安全；PC/停顿样本占比仍不等于逐行耗时。
本轮修复依据：ELF 允许同名节（[ELF 规范](https://refspecs.linuxfoundation.org/elf/gabi4+/ch4.sheader.html)）；重复设备节仍必须拒绝，不能覆盖或取第一个。
