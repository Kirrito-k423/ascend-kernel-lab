"""PR45 反馈回归：重复节与退出码为 0 的不可用解码输出。仅 Host mock。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_simt_target import elf, target


class FeedbackTests(unittest.TestCase):
    def test_duplicate_sections_and_zero_exit_diagnostics(self):
        for case in ("groups", "duplicate-device", "placeholder", "partial", "dwarf-error",
                     "aicore", "aicore-placeholder", "lookalike"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                repo = Path(folder)
                source = repo / "kernels/elastic_dispatch.cpp"
                source.parent.mkdir()
                source.write_text("void LoadSqCqContexts() {}\n")
                device = elf({".text": b"code", ".debug_line": b"lines"})
                parts = [(".group", b"first"), (".group", b"second"), (".aicore_binary", device)]
                if case == "duplicate-device":
                    parts.append((".aicore_binary", device))
                original = elf(parts, machine=183)
                self.assertEqual(target.sections(original)[".group"], [b"first", b"second"])
                obj, output = repo / "target.o", repo / "evidence"
                obj.write_bytes(original)

                def fake(cmd, out, name, manifest, timeout, required=True):
                    self.assertIn(Path(cmd[0]).name, ("git", "llvm-objdump", "llvm-dwarfdump"))
                    text = ".text LoadSqCqContexts\n"
                    if name == "objdump-help":
                        text = "  --disassemble-aicore    Display AICore instructions\n" if case.startswith("aicore") else ""
                        if case == "lookalike":
                            text = "  --disassemble-aicore-unsupported\n"
                    if name == "assembly":
                        self.assertEqual("--disassemble-aicore" in cmd, case.startswith("aicore"))
                        text = "  16398: 00 00 00 00 mock_instruction\n"
                        if case in ("placeholder", "partial", "aicore-placeholder"):
                            text = (text if case == "partial" else "") + "  163a0: <not available>\n"
                    if name == "line-table" and case == "dwarf-error":
                        text = "device.o: Error in creating MCRegInfo\n"
                    manifest["commands"].append({"exit_code": 0, "log": name + ".log"})
                    (out / (name + ".log")).write_text(text)
                    return text

                argv = ["collect", "--repo", str(repo), "--object", str(obj), "--output", str(output)]
                complete = case in ("groups", "aicore", "lookalike")
                with patch.object(sys, "argv", argv), patch.object(target, "execute", fake):
                    self.assertEqual(target.main(), int(not complete))
                manifest = json.loads((output / "manifest.json").read_text())
                expected = "collected_pending_review" if complete else "inspection_incomplete"
                self.assertEqual(manifest["status"], "failed" if case == "duplicate-device" else expected)
                self.assertEqual(manifest["mapping"], "pending_review")
                self.assertFalse(manifest["hardware_executed"])
                self.assertEqual(obj.read_bytes(), original)
                with zipfile.ZipFile(str(output) + ".zip") as bundle:
                    if case != "duplicate-device":
                        self.assertEqual(bundle.read("simt_probe/device.o"), device)
                        self.assertEqual(manifest["assembly_unavailable_count"], int(case in ("placeholder", "partial", "aicore-placeholder")))
                        self.assertEqual(manifest["dwarf_error_observed"], case == "dwarf-error")
                    else:
                        self.assertNotIn("simt_probe/device.o", bundle.namelist())
