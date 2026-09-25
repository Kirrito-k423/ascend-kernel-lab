#!/usr/bin/env python3
"""只读收集目标源码与设备 ELF 映射证据；不加载共享库、不启动 NPU 或 profiler。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct

from run_simt_probe import execute, pack_result


def sections(data):
    # 当前支持普通 ELF64 小端节表；拒绝扩展编号，避免误切出另一个设备镜像。
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("需要 ELF64 小端文件")
    offset = struct.unpack_from("<Q", data, 40)[0]
    width, count, names_index = struct.unpack_from("<HHH", data, 58)
    if width != 64 or not count or names_index >= count or offset + width * count > len(data):
        raise ValueError("节表越界或使用不支持的扩展编号")
    rows = [struct.unpack_from("<IIQQQQIIQQ", data, offset + i * width) for i in range(count)]
    for row in rows:
        if row[1] != 8 and row[4] + row[5] > len(data):  # SHT_NOBITS 没有文件内容。
            raise ValueError("ELF 节内容越界")
    names_row = rows[names_index]
    names = data[names_row[4]:names_row[4] + names_row[5]]
    result = {}
    for row in rows:
        if row[0] >= len(names):
            raise ValueError("节名越界")
        name = names[row[0]:].split(b"\0", 1)[0].decode("utf-8", errors="replace")
        if name in result:
            raise ValueError("重复节名，需单独审查镜像边界")
        result[name] = data[row[4]:row[4] + row[5]] if row[1] != 8 else b""
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--object", type=Path, required=True, help="优先选 elastic_dispatch.cpp.o")
    parser.add_argument("--source", default="kernels/elastic_dispatch.cpp")
    parser.add_argument("--function", default="LoadSqCqContexts")
    parser.add_argument("--device-elf", action="store_true", help="输入本身已是独立设备 ELF")
    parser.add_argument("--tool-dir", type=Path, help="匹配 CANN 的 LLVM 工具目录")
    parser.add_argument("--context", type=Path, help="启动命令/rank/shape/构建参数文本，只归档不执行")
    parser.add_argument("--compile-commands", type=Path, help="可选的 compile_commands.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    if args.timeout <= 0 or not args.function:
        parser.error("timeout 必须为正，function 不能为空")
    repo, obj, output = args.repo.resolve(), args.object.resolve(), args.output.resolve()
    archive = Path(str(output) + ".zip")
    if archive.exists():
        parser.error("ZIP 已存在，请使用新 output")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    manifest = {"schema": "akl.simt_target.v1", "status": "incomplete", "commands": [],
                "function": args.function, "hardware_executed": False,
                "runtime_classification": "unknown", "replay_safe": None, "mapping": "pending_review"}
    try:
        def run(cmd, name):
            return execute(cmd, output, name, manifest, args.timeout, required=False)
        source = (repo / args.source).resolve()
        source.relative_to(repo)
        source_data, object_data = source.read_bytes(), obj.read_bytes()
        manifest.update(source_sha256=hashlib.sha256(source_data).hexdigest(), source_path=str(source),
                        object_sha256=hashlib.sha256(object_data).hexdigest(), object_path=str(obj))
        (output / "target-source.cpp").write_bytes(source_data)
        shutil.copy2(__file__, output / Path(__file__).name)
        if args.context:
            shutil.copyfile(args.context, output / "run-context.txt")
        database = args.compile_commands or repo / "build/compile_commands.json"
        entries = []
        if database.is_file():
            for entry in json.loads(database.read_text()):
                if (Path(entry["directory"]) / entry["file"]).resolve() == source:
                    entries.append(entry)
        (output / "compile-command.json").write_text(json.dumps(entries, indent=2))
        manifest["compile_entries"] = len(entries)
        run(["git", "-C", repo, "rev-parse", "HEAD"], "revision")
        run(["git", "-C", repo, "status", "--short"], "worktree")
        outer = sections(object_data)
        manifest["input_sections"] = list(outer)
        device = object_data if args.device_elf else outer.get(".aicore_binary", b"")
        if not device:
            raise ValueError("没有 .aicore_binary；请提供该翻译单元的 .o 或显式 --device-elf")
        if device.count(b"\x7fELF") != 1:
            raise ValueError("设备节有多个 ELF 标记，暂不支持拼接镜像；请回传证据")
        inner = sections(device)
        machine = struct.unpack_from("<H", device, 18)[0]
        if machine in (62, 183):
            raise ValueError("输入仍是 x86-64/AArch64 Host ELF，不能当成设备映射")
        elf = output / "device.o"
        elf.write_bytes(device)
        manifest.update(device_sha256=hashlib.sha256(device).hexdigest(), elf_machine=machine,
                        device_sections=list(inner), debug_line_present=".debug_line" in inner,
                        lccl_section_present="Attr_Section_Lcal" in inner)
        cann = Path(os.environ["ASCEND_HOME_PATH"]) if os.environ.get("ASCEND_HOME_PATH") else None
        directories = [args.tool_dir] if args.tool_dir else []
        if cann and not args.tool_dir:
            directories += [cann / p for p in ("tools/bisheng_compiler/bin", "compiler/ccec_compiler/bin", "bin")]
        def tool(name):
            for directory in directories:
                path = directory / name
                if path.is_file():
                    return str(path)
            return str(args.tool_dir / name) if args.tool_dir else shutil.which(name) or name
        objdump, dwarf = tool("llvm-objdump"), tool("llvm-dwarfdump")
        run([objdump, "--version"], "objdump-version")
        run([dwarf, "--version"], "dwarf-version")
        symbols = run([objdump, "--syms", "--demangle", elf], "symbols")
        info = run([dwarf, "--name=" + re.escape(args.function), "--regex", elf], "function-dwarf")
        run([dwarf, "--debug-line", elf], "line-table")
        run([objdump, "-d", "--demangle", "--line-numbers", elf], "assembly")
        # 名称命中与 debug_line 存在都只是线索，不等于该函数的 PC/行映射已验证。
        manifest.update(status="collected_pending_review",
                        symbol_name_observed=any(args.function in l and ".text" in l for l in symbols.splitlines()),
                        debug_name_observed=any(args.function in l and "DW_AT_" in l and "name" in l for l in info.splitlines()))
        if any(c["exit_code"] for c in manifest["commands"]):
            manifest["status"] = "inspection_incomplete"
    except (Exception, KeyboardInterrupt) as error:
        manifest.update(status="failed", error=str(error) or "interrupted")
    finally:
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
        try:
            pack_result(output, archive)
            print(f"{manifest['status']}；回传 {archive}（{archive.stat().st_size} 字节）", flush=True)
        except (OSError, RuntimeError) as error:
            manifest["archive_error"] = str(error)
            (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
            print(f"打包失败：{error}", flush=True)
    return int(manifest["status"] != "collected_pending_review" or "archive_error" in manifest)


if __name__ == "__main__":
    raise SystemExit(main())
