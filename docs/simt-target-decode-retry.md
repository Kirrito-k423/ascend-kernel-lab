# PR46 后续：复用设备 ELF，验证 AICore 解码与源码定位

本轮只读 PR46 已生成的设备 ELF，不重编、不替换业务库、不运行 NPU 或 profiler。直接使用本分支即可取得依赖的收集器修复。

## 1. 运行步骤

```bash
git fetch origin codex/simt-target-decode-retry
git switch --detach origin/codex/simt-target-decode-retry
source /实际CANN安装目录/set_env.sh
DEEPEP=/实际路径/ascend_deepep
ELF=/上轮实验目录/results/simt-target-debug/device.o
python3 scripts/collect_simt_target.py --repo "$DEEPEP" --device-elf --object "$ELF" \
  --output results/simt-target-aicore
```

新收集器保存 `objdump-help.log`，当帮助中声明 `--disassemble-aicore` 时，使用 `-d --disassemble-aicore --demangle --line-numbers`。
PR46 实际 CANN 帮助声明了这个选项；此前仅用 `-d`。这是依据本机帮助提出的待验证修正，尚不能保证 SIMT 指令可解码。
查看 manifest 的 `disassembly_options` 和 `assembly.log`。若系统 dwarfdump 仍报 MCRegInfo，整体状态仍为 `inspection_incomplete`；保留报告，继续下面的独立交叉核查。

使用此次安装中已找到的 `tools/msopprof/bin/llvm-symbolizer`。只直接调用这个地址定位程序，不运行 msopprof；参数沿用[官方地址解析实现](https://github.com/Ascend/msopprof/blob/7e847da532c7fdfafa32af98bedd823a13e1ea23/csrc/op_profiling/profiling/op_prof_data_parse.cpp#L201)。
以下地址只适用于本次固定哈希的设备 ELF；脚本先验证哈希，拒绝拿换过的对象套用地址。

```bash
python3 - "$ELF" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, "scripts")
from run_simt_probe import execute, pack_result

elf = Path(sys.argv[1]).resolve()
out = Path("results/simt-target-symbolizer").resolve()
archive = Path(str(out) + ".zip")
if archive.exists():
    raise SystemExit("ZIP 已存在，请保留旧包并改用新输出目录")
out.mkdir(parents=True, mode=0o700, exist_ok=False)
record = {"schema": "akl.simt_symbolizer.v1", "status": "incomplete", "commands": [],
          "hardware_executed": False, "mapping": "pending_review"}
try:
    digest = hashlib.sha256(elf.read_bytes()).hexdigest()
    record["device_sha256"] = digest
    if digest != "a434f3eae41a37df4b95f7d72c30f88576e28f13efe3d3b1e3d85e378e147bf5":
        raise ValueError("设备 ELF 已变；先回传新 manifest，不复用固定 PC")
    tool = Path(os.environ["ASCEND_HOME_PATH"]) / "tools/msopprof/bin/llvm-symbolizer"
    record["symbolizer_sha256"] = hashlib.sha256(tool.read_bytes()).hexdigest()
    execute([tool, "--version"], out, "version", record, 120)
    pcs = ["0x16268", "0x16288", "0x16290", "0x165e8", "0x16730", "0x16a08", "0x16b40", "0x3dc0"]
    execute([tool, "-f", "-e", elf, "--inlining=true", "--output-style=JSON", *pcs],
            out, "lookup", record, 120)
    record["status"] = "collected_pending_review"
except (Exception, KeyboardInterrupt) as error:
    record.update(status="failed", error=str(error) or "interrupted")
finally:
    (out / "manifest.json").write_text(json.dumps(record, indent=2))
    pack_result(out, archive)
print(record["status"], archive)
raise SystemExit(int(record["status"] != "collected_pending_review"))
PY
```

每条工具命令最多 120 秒；错误和日志一并打包。以上代码不读取或执行归档的业务启动脚本。

## 2. 回传与验收

回传 `results/simt-target-aicore.zip` 和 `results/simt-target-symbolizer.zip`，每包严格小于 5 MB；失败也回传。
若打包超限，原目录保留，按[原 SOP 的状态包步骤](simt-target-preflight.md)发送 manifest。无需再发业务 Host 对象或重复做 `-g` 编译。
先核对新 manifest 的设备 SHA256 与上述固定值相同；源码 SHA256 应为 `3207852877bd8292867e27c18f5c2974eee2d6bb06efc2f01082f497add05c51`，若不同，先处理版本不一致。

| 证据 | 本轮判定 |
| --- | --- |
| `disassembly_options` | 应记录 `-d` 与 `--disassemble-aicore` |
| 目标范围 `[0x16268, 0x16b40)` | 需要真实助记符/操作数；只有地址、原始字节或 `<not available>` 都不算解码完成 |
| `0x16268 / 0x165e8 / 0x16730 / 0x16a08` | 行表预期为目标源码 1431 / 1473 / 1483 / 1494 行；核对 symbolizer 的文件、行号与内联栈 |
| `0x16288 / 0x16290` | 分别是头文件行和 line 0 对照；不能把缺失行号强行填成邻近业务行 |
| `0x16b40 / 0x3dc0` | 分别是目标范围外地址和 VF_CALL 包装层对照；不能计入目标函数体 |

即使 symbolizer 返回 0，也需检查实际输出；`??`、行号 0 或诊断信息都不能作为有效关联。MCRegInfo 警告保留，不能为了“成功”删除。
如果 AICore 开关仍不能解码，回传完整日志；暂不尝试猜测 CPU 型号、替换 ELF 架构字段或运行通信重放。

## 3. PR46 已核验的进展

以下是[用户回传](https://github.com/Kirrito-k423/ascend-kernel-lab/pull/46#issuecomment-5827144482)的离线核对结论，不是新增硬件运行结果：

- 原 Host 对象直接提取得到的设备 ELF 与 PR45 独立提取结果逐字节相同，提取修复已获真实对象验证。
- 两份完整编译命令的差异仅为增加 `-g` 和输出路径；源码快照相同。
- 调试设备 ELF 实际为 2,253,952 字节；`.text` 为 105,528 字节。评论中的尺寸口径有差异，以附件字节数为准。
- 目标函数范围从 `[0x16398, 0x16c70)` 变成 `[0x16268, 0x16b40)`，其 2,264 字节机器码完全相同；整份 ELF/其他函数不能据此视为相同。
- Linux LLVM 17 与本地 LLVM 分别解析同一设备 ELF，输出的行表数据行一致，二者均保留 MCRegInfo 诊断。
- 真正覆盖 VF 的行表中，文件索引 25 是目标源码；不能把另一个行表中的 `file_names[0]` 当成它的文件索引。
- 233 个占位反汇编地址中，211 个关联目标源码 31 行，1 个关联内置头文件，21 个为 line 0。尚未取得这些地址的可读指令或采样计数。

这些数字仅用于验证地址/源码关联，不能画成耗时或热点占比。目标函数名出现在 `VF_CALL` 的内联信息中，也不代表包装层地址就是目标 VF 地址。
本地测试覆盖帮助选项存在/缺失/近似名称，以及启用开关后仍输出占位符的情形；真实解码效果等待本轮回传。
