#!/usr/bin/env python3
"""离线检查 Ascend950 SIMT 指令名称；不解码操作数，不产生采样或耗时。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

from collect_simt_target import sections

REVISION = "7e847da532c7fdfafa32af98bedd823a13e1ea23"
PREFIX = "csrc/op_profiling/instr_encoding/"
HEADERS = ("instr_encoding.h", "encodingA5/VectorInstrTableA5.h")


def block(text, name):
    matches = re.findall(r"\b" + re.escape(name) + r"\s*=\s*\{(.*?)\n\};", text, re.S)
    if len(matches) != 1:
        raise ValueError(f"指令表定义不唯一：{name}")
    return matches[0]


def load_tables(repo):
    # 只读取官方固定提交中的数据，不导入或执行外部仓库代码。
    headers = [subprocess.check_output(["git", "-C", str(repo), "show", f"{REVISION}:{PREFIX}{p}"])
               for p in HEADERS]
    groups, names = [h.decode() for h in headers]
    result = {}
    for width, count in ((8, 10), (16, 8)):
        result[width] = []
        for line in block(groups, f"MASK2GROUPS_A5_{width * 8}BIT").splitlines():
            tables = re.findall(r"INSTR_VEC_TABLE\d+_A5", line)
            if not tables:
                continue
            table, = tables
            mask = tuple(int(h, 16) for h in re.findall(r"0x[0-9a-f]+", line))
            entries = {}
            for key, name in re.findall(r'\{\{?([0-9a-fx, ]+)\}?,\s*"([^"]+)"\},', block(names, table)):
                words = tuple(int(h, 16) for h in key.split(","))
                if len(words) != width // 8 or words in entries:
                    raise ValueError("指令表键宽度错误或重复")
                entries[words] = name
            if len(mask) != width // 8 or not entries:
                raise ValueError("掩码宽度错误或指令表为空")
            result[width].append((mask, entries))
        if len(result[width]) != count:
            raise ValueError("指令掩码组数量不符")
    return result, {p: hashlib.sha256(h).hexdigest() for p, h in zip(HEADERS, headers)}


def classify(raw, tables):
    # Enc128 按小端 low64/high64 分别与掩码相与；不是把两个字交换后拼接。
    words = struct.unpack("<" + "Q" * (len(raw) // 8), raw)
    matches = {entries[key] for mask, entries in tables[len(raw)]
               if (key := tuple(w & m for w, m in zip(words, mask))) in entries}
    # 官方采用首次命中；诊断工具保守保留所有匹配，冲突时不猜测指令名称。
    return sorted(matches)


def audit(capture, tables):
    files = {name: (capture / name).read_bytes() for name in
             ("device.o", "target-source.cpp", "symbols.log", "assembly.log")}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    manifest = json.loads((capture / "manifest.json").read_text())
    if (hashes["device.o"] != manifest["device_sha256"] or
            hashes["target-source.cpp"] != manifest["source_sha256"]):
        raise ValueError("设备或源码 SHA256 与采集清单不符")
    device = files["device.o"]
    text, = sections(device)[".text"]
    elf_type, machine = struct.unpack_from("<HH", device, 16)
    if elf_type not in (1, 2) or machine != 4137:
        raise ValueError("仅支持本轮 Ascend 设备的 ET_REL/ET_EXEC ELF（machine=4137）")
    offset = struct.unpack_from("<Q", device, 40)[0]
    count, names_index = struct.unpack_from("<HH", device, 60)
    rows = [struct.unpack_from("<IIQQQQIIQQ", device, offset + i * 64) for i in range(count)]
    names_row = rows[names_index]
    names = device[names_row[4]:names_row[4] + names_row[5]]
    row, = [r for r in rows if names[r[0]:].split(b"\0", 1)[0] == b".text"]
    if row[3] != 0:
        raise ValueError("仅支持 .text 地址为零的设备对象，不能将重定位 PC 当成文件偏移")
    function = manifest["function"]
    symbols = re.findall(r"(?m)^\s*([0-9a-f]+)\s+\w\s+F\s+\.text\s+([0-9a-f]+)\s+(.+)$",
                         files["symbols.log"].decode())
    bounds = [(int(pc, 16), int(size, 16)) for pc, size, name in symbols
              if name.startswith(function + "(") and name.endswith("(.vector_simt_entry)")]
    if len(bounds) != 1:
        raise ValueError("目标函数必须有唯一 .text 函数符号")
    start, size = bounds[0]
    end = start + size
    pcs = [int(pc, 16) for pc in re.findall(r"(?m)^\s*([0-9a-f]+):\s*<not available>\s*$",
                                          files["assembly.log"].decode())]
    pcs = [pc for pc in pcs if start <= pc < end]
    if not (0 <= start < end <= len(text)) or not pcs or pcs[0] != start or pcs != sorted(set(pcs)):
        raise ValueError("指令 PC 不完整、重复、乱序或符号越界")
    instructions = []
    for pc, stop in zip(pcs, pcs[1:] + [end]):
        if stop - pc not in (8, 16):
            raise ValueError("仅接受 8/16 字节 SIMT 指令；检查占位符是否缺失")
        raw = text[pc:stop]
        matches = classify(raw, tables)
        instructions.append({"pc": hex(pc), "size": len(raw), "raw": raw.hex(), "matches": matches,
                             "opcode": matches[0] if len(matches) == 1 else None})
    return {"schema": "akl.simt_opcodes.v1", "chip": "Ascend950", "chip_basis": "user_selected",
            "scope": "opcode_only", "hardware_executed": False, "function": function,
            "input_sha256": hashes, "start": hex(start), "end_exclusive": hex(end),
            "status": "classified" if all(i["opcode"] for i in instructions) else "partial",
            "operands": None, "line_time": None, "samples": None, "instructions": instructions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True, help="已解压的 simt_probe 目录")
    parser.add_argument("--msopprof", type=Path, required=True, help="含固定官方提交的源码仓")
    parser.add_argument("--chip", choices=["Ascend950"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tables, hashes = load_tables(args.msopprof)
    report = audit(args.capture, tables)
    report.update(table_revision=REVISION, table_sha256=hashes,
                  table_source="https://github.com/Ascend/msopprof")
    with args.output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(f"{report['status']}: {len(report['instructions'])} 条静态指令，输出 {args.output}")
    return 0 if report["status"] == "classified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
