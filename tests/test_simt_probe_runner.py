"""只验证 Host 收集流程；伪采样文件不是 NPU 数据。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile
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
        for scenario, metric in (("capture", "PcSampling"), ("capture", "PCSampling"),
                                 ("missing-report", "PcSampling"), ("bad-baseline", "PcSampling"),
                                 ("unsupported", "PCSamplingExtra")):
            with self.subTest(scenario=scenario, metric=metric), tempfile.TemporaryDirectory() as folder:
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
                    if name == "profile":
                        self.assertIn("--aic-metrics=" + metric, cmd)
                    if name == "profile" and scenario == "capture":
                        (out / "profile").mkdir()
                        (out / "profile/visualize_data.bin").write_bytes(b"mock, not samples")
                    return "TYPE: Source | " + metric + " | PipeTimeline"

                argv = ["probe", "--device", "0", "--profile", "--output", str(output)]
                with patch.object(sys, "argv", argv), patch.object(probe, "execute", fake), \
                     patch.dict(os.environ, {"ASCEND_HOME_PATH": folder}), \
                     patch.object(probe.shutil, "which", return_value="/mock/msopprof"):
                    self.assertEqual(probe.main(), int(scenario != "capture"))
                result = json.loads((output / "manifest.json").read_text())
                expected = "captured_pending_review" if scenario == "capture" else "failed"
                self.assertEqual(result["status"], expected)
                self.assertEqual(calls.count("profile"), int(scenario not in ("bad-baseline", "unsupported")))
                with zipfile.ZipFile(str(output) + ".zip") as bundle:
                    self.assertIn("simt_probe/manifest.json", bundle.namelist())

    def test_zip_limit_preserves_raw_results(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "result"
            output.mkdir()
            (output / "log").write_text("evidence")
            archive = Path(folder) / "result.zip"
            with self.assertRaises(RuntimeError):
                probe.pack_result(output, archive, limit=1)
            self.assertFalse(archive.exists())
            self.assertFalse(Path(str(archive) + ".partial").exists())
            self.assertEqual((output / "log").read_text(), "evidence")

    def test_invalid_replay_controls_do_not_start_commands(self):
        for flags in (["--host-launches", "2"], ["--replay-mode", "application"],
                      ["--stateful", "--host-launches", "2", "--profile"]):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / "capture"
                argv = ["probe", "--device", "0", "--output", str(output)] + flags
                with patch.object(sys, "argv", argv), patch.object(probe, "execute") as execute:
                    with self.assertRaises(SystemExit):
                        probe.main()
                    execute.assert_not_called()
                self.assertFalse(output.exists())

    def test_state_observations_and_failed_replay_are_preserved(self):
        cases = [("plain", "kernel", 1), ("plain", "kernel", 2), ("ok", "kernel", 1),
                 ("ok", "application", 1), ("extra", "kernel", 1), ("missing", "kernel", 1),
                 ("unfinished", "application", 1), ("bad-checksum", "kernel", 1),
                 ("bad-baseline", "kernel", 1), ("nonzero", "kernel", 1)]
        for scenario, mode, launches in cases:
            with self.subTest(case=(scenario, mode, launches)), tempfile.TemporaryDirectory() as folder:
                output, calls = Path(folder) / "capture", []

                def fake(cmd, out, name, manifest, timeout, required=True):
                    calls.append(name)
                    log = "PCSampling --replay-mode kernel application\n"
                    if name == "build":
                        (out / "build").mkdir()
                        (out / "build/akl_simt_probe").write_bytes(b"mock, not ELF")
                    if name in ("baseline", "profile"):
                        self.assertEqual([str(x) for x in cmd[-2:]], [str(launches), "1"])
                        log = ""
                        for pid in range(100, 102 if mode == "application" and name == "profile" else 101):
                            count = 24 if (scenario == "extra" and name == "profile") or \
                                (scenario == "bad-baseline" and name == "baseline") else launches
                            row = {"pid": pid, "expected": launches, "counts": [count] * 32}
                            log += f"AKL_APP_START pid={pid}\nAKL_STATE {json.dumps(row)}\n"
                            log += "PASS soc=mock checked=32\n"
                        if name == "profile":
                            self.assertIn("--replay-mode=" + mode, cmd)
                            if scenario == "missing":
                                log = "no state output\n"
                            if scenario == "unfinished":
                                log += "AKL_APP_START pid=999\n"
                            if scenario == "bad-checksum":
                                log = log.replace("PASS soc=mock checked=32\n", "checksum failed\n")
                            (out / "profile").mkdir()
                            (out / "profile/visualize_data.bin").write_bytes(b"mock, not samples")
                    (out / (name + ".log")).write_text(log)
                    if scenario == "nonzero" and name == "profile":
                        raise RuntimeError("profiler failed after application completed")
                    return log

                argv = ["probe", "--device", "0", "--stateful", "--host-launches", str(launches),
                        "--output", str(output)]
                if scenario != "plain":
                    argv += ["--profile", "--replay-mode", mode]
                success = scenario in ("plain", "ok")
                with patch.object(sys, "argv", argv), patch.object(probe, "execute", fake), \
                     patch.dict(os.environ, {"ASCEND_HOME_PATH": folder}), \
                     patch.object(probe.shutil, "which", return_value="/mock/msopprof"):
                    self.assertEqual(probe.main(), int(not success))
                result = json.loads((output / "manifest.json").read_text())
                expected = ("baseline_passed" if scenario == "plain" else "captured_pending_review")
                self.assertEqual(result["status"], expected if success else "failed")
                self.assertEqual(calls.count("profile"), int(scenario not in ("plain", "bad-baseline")))
                if scenario == "extra":
                    self.assertEqual(result["state_observations"]["profile"]["records"][0]["counts"], [24] * 32)
                with zipfile.ZipFile(str(output) + ".zip") as bundle:
                    self.assertEqual(json.loads(bundle.read("simt_probe/manifest.json")), result)


if __name__ == "__main__":
    unittest.main()
