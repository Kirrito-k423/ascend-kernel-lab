# SIMT 指令名称离线核对

本轮在有网络的普通主机运行，不需要 A5、CANN 或重新编译业务。输入复用 [PR47 回传附件](https://github.com/Kirrito-k423/ascend-kernel-lab/pull/47#issuecomment-5828160081) 中解压的 `simt_probe` 目录；不执行附件中的脚本或二进制。

## 运行

```bash
git fetch origin codex/simt-opcode-checks
git switch --detach origin/codex/simt-opcode-checks
mkdir -p results
git init results/msopprof-source
git -C results/msopprof-source fetch --depth=1 https://github.com/Ascend/msopprof.git \
  7e847da532c7fdfafa32af98bedd823a13e1ea23
CAPTURE=/已有解压目录/simt_probe
python3 scripts/audit_simt_opcodes.py --chip Ascend950 \
  --capture "$CAPTURE" --msopprof results/msopprof-source \
  --output results/simt-opcodes.json
MSOPPROF_SOURCE="$PWD/results/msopprof-source" \
  python3 -m unittest discover -s tests -p 'test_simt*.py' -v
```

需 Python 3.8+、Git。固定提交只读取两个指令表，不编译或执行 msopprof。没有网络时可复制含该提交的仓库，并调整 `--msopprof`。
输出文件必须不存在；重复运行换新名称。退出码 0 表示每个 PC 均有唯一名称，2 表示包含未知或歧义，输入不满足约束则报错。
如需复核，仅回传生成的 JSON（本轮约 38 KB）；不需要重新上传原始 ELF。

## 已核对的真实附件

| 证据 | 结论 |
| --- | --- |
| PR47 的 `--disassemble-aicore` | 全文件仍有 25,256 个占位符；未得到操作数 |
| 目标 `[0x16268, 0x16b40)` | **233** 条静态指令，共 2,264 字节；评论的 448 个地址不符 |
| 固定官方表逐条识别 | 233/233 唯一名称；183 条 8 字节、50 条 16 字节 |
| 先前小例子真实 profiler 报告 | ProbeContexts 的 27/27 条名称与离线识别一致 |
| symbolizer 的 8 个控制地址 | 与 DWARF 一致；头文件、Line 0、函数末尾、调用包装层分别保留 |

本轮设备 SHA256 为 `a434f3eae41a37df4b95f7d72c30f88576e28f13efe3d3b1e3d85e378e147bf5`。
结合 PR46 行表，233 个 PC 中 211 个对应 31 行目标源码，1 个对应头文件，21 个为未知行；不能用外层内联帧覆盖 Line 0。

## 方法与边界

依据 [官方编码匹配实现](https://github.com/Ascend/msopprof/blob/7e847da532c7fdfafa32af98bedd823a13e1ea23/csrc/op_profiling/instr_encoding/instr_encoding.cpp) 和同提交的 Ascend950 编码表：用占位符 PC 间距取得 8/16 字节机器码，再按掩码识别名称。诊断工具保守检查所有匹配，多个不同名称时输出 `null`。
仅接受唯一 SIMT 函数符号、machine=4137 的 ET_REL/ET_EXEC、地址为零的 `.text`；芯片由用户显式选择，ELF machine 不能证明型号。拒绝哈希不符、越界、重复/乱序 PC 或不支持的指令宽度。
JSON 保存输入与编码表哈希、官方提交、PC、原始字节、候选名称。`opcode_only` 表示**指令名称**，不包含寄存器、立即数或访存地址的反汇编。
`samples`、`line_time`、`operands` 保持 `null`；静态指令数量不是执行次数、热点或耗时。采集 manifest 仍保留原始失败状态，诊断结果另存，不能把 `classified` 当成整体采样成功。

下一阶段可将这些名称加入源码/PC 视图，但真实耗时仍缺证据。通信 kernel 的 replay 安全性需要独立实验：既有小例子 kernel replay 将状态累计 24 次，application replay 则重启应用；这不证明真实多 rank 通信可安全重放。暂不把该诊断脚本接到通信采样命令，也不据此填写耗时柱状图。
