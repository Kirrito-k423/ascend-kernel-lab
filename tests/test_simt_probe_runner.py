"""只验证 Host 收集流程；伪采样文件不是 NPU 数据。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("probe", ROOT / "scripts/run_simt_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ProbeRunnerTests(unittest.TestCase):
    def test_command_failure_and_timeout_are_recorded_once(self):
        with tempfile.TemporaryDirectory() as folder:
            output, manifest = Path(folder), {"commands": []}
            with self.assertRaises(RuntimeError):
                probe.execute([sys.executable, "-c", "raise SystemExit(7)"],
                              output, "failure", manifest, 5)
            with self.assertRaises(subprocess.TimeoutExpired):
                probe.execute([sys.executable, "-c", "import time; time.sleep(5)"],
                              output, "timeout", manifest, 0.1)
            self.assertEqual([r["exit_code"] for r in manifest["commands"]], [7, 124])
            self.assertEqual(json.loads((output / "manifest.json").read_text()), manifest)

    def test_capture_and_failure_bundles(self):
        for scenario in ("capture", "missing-report", "bad-baseline"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / "capture"
                calls = []

                def fake(cmd, out, name, manifest, timeout, required=True):
                    calls.append(name)
                    (out / (name + ".log")).write_text("host workflow mock\n")
                    if name == "build":
                        (out / "build").mkdir()
                        (out / "build/akl_simt_probe").write_bytes(b"mock, not ELF")
                    if name == "baseline" and scenario == "bad-baseline":
                        raise RuntimeError("oracle failed")
                    if name == "profile" and scenario == "capture":
                        (out / "profile").mkdir()
                        (out / "profile/visualize_data.bin").write_bytes(b"mock, not samples")
                    return "PcSampling"

                argv = ["probe", "--device", "0", "--profile", "--output", str(output)]
                with patch.object(sys, "argv", argv), patch.object(probe, "execute", fake), \
                     patch.dict(os.environ, {"ASCEND_HOME_PATH": folder}), \
                     patch.object(probe.shutil, "which", return_value="/mock/msopprof"):
                    self.assertEqual(probe.main(), int(scenario != "capture"))
                result = json.loads((output / "manifest.json").read_text())
                expected = "captured_pending_review" if scenario == "capture" else "failed"
                self.assertEqual(result["status"], expected)
                self.assertEqual(calls.count("profile"), int(scenario != "bad-baseline"))
                with tarfile.open(str(output) + ".tar.gz") as bundle:
                    self.assertIn("simt_probe/manifest.json", bundle.getnames())


if __name__ == "__main__":
    unittest.main()
