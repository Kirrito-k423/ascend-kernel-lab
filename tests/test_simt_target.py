"""ELF 格式与收集流程测试；工具输出为 mock，不是 NPU 映射证据。"""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collect_simt_target as target


def elf(parts, machine=4137):
    names = b"\0.shstrtab\0" + b"".join(n.encode() + b"\0" for n in parts)
    entries = [(".shstrtab", names)] + list(parts.items())
    data, rows = bytearray(64), [(0,) * 10]
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", data, 18, machine)
    for name, content in entries:
        rows.append((names.index(name.encode()), 3 if name == ".shstrtab" else 1,
                     0, 0, len(data), len(content), 0, 0, 1, 0))
        data.extend(content)
    struct.pack_into("<Q", data, 40, len(data))
    struct.pack_into("<HHH", data, 58, 64, len(rows), 1)
    return bytes(data) + b"".join(struct.pack("<IIQQQQIIQQ", *r) for r in rows)


class TargetTests(unittest.TestCase):
    def test_section_bounds_and_format(self):
        data = elf({".debug_line": b"lines"})
        self.assertEqual(target.sections(data)[".debug_line"], b"lines")
        bad_offset = bytearray(data)
        struct.pack_into("<Q", bad_offset, 40, len(data) + 1)
        for bad in (b"", data[:63], data[:5] + b"\x02" + data[6:], data[:-1], bad_offset):
            with self.subTest(bad=bytes(bad[:8])), self.assertRaises(ValueError):
                target.sections(bad)

    def test_readonly_collection_and_failure_bundles(self):
        for case in ("ok", "no-debug", "bad-tool", "host", "missing", "multiple"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                repo = Path(folder)
                source = repo / "kernels/elastic_dispatch.cpp"
                source.parent.mkdir()
                source.write_text("void LoadSqCqContexts() {}\n")
                device = elf({".text": b"code", **({} if case == "no-debug" else {".debug_line": b"lines"})})
                if case == "host":
                    device = elf({".text": b"host"}, machine=183)
                if case == "multiple":
                    device += device
                original = elf({} if case == "missing" else {".aicore_binary": device}, machine=183)
                obj = repo / "elastic_dispatch.cpp.o"
                obj.write_bytes(original)
                context = repo / "context.txt"
                context.write_text("touch SHOULD_NOT_EXECUTE\n")
                output = repo / "evidence"

                def fake(cmd, out, name, manifest, timeout, required=True):
                    self.assertIn(Path(cmd[0]).name, ("git", "llvm-objdump", "llvm-dwarfdump"))
                    rc = int(case == "bad-tool" and name == "assembly")
                    manifest["commands"].append({"exit_code": rc, "log": name + ".log"})
                    text = '.text LoadSqCqContexts\nDW_AT_name ("LoadSqCqContexts")\n'
                    (out / (name + ".log")).write_text(text)
                    return text

                argv = ["collect", "--repo", str(repo), "--object", str(obj), "--context", str(context),
                        "--output", str(output), "--tool-dir", str(repo / "tools")]
                with patch.object(sys, "argv", argv), patch.object(target, "execute", fake):
                    self.assertEqual(target.main(), int(case not in ("ok", "no-debug")))
                manifest = json.loads((output / "manifest.json").read_text())
                self.assertEqual(obj.read_bytes(), original)
                self.assertEqual(manifest["mapping"], "pending_review")
                self.assertFalse(manifest["hardware_executed"])
                self.assertIsNone(manifest["replay_safe"])
                if case in ("ok", "no-debug", "bad-tool"):
                    self.assertEqual((output / "device.o").read_bytes(), device)
                    self.assertEqual(manifest["debug_line_present"], case != "no-debug")
                with zipfile.ZipFile(str(output) + ".zip") as bundle:
                    self.assertEqual(bundle.read("simt_probe/run-context.txt"), context.read_bytes())
                    self.assertIn("simt_probe/manifest.json", bundle.namelist())
                self.assertFalse((repo / "SHOULD_NOT_EXECUTE").exists())


if __name__ == "__main__":
    unittest.main()
