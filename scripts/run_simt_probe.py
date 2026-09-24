#!/usr/bin/env python3
"""构建并采集独立 SIMT 探针；失败也保留回传包，不自动重试。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tarfile
import time


def execute(cmd, output, name, manifest, timeout, required=True):
    record = {"command": [str(x) for x in cmd], "log": name + ".log"}
    manifest["commands"].append(record)
    started = time.monotonic()
    with (output / record["log"]).open("w") as log:
        try:
            with subprocess.Popen(record["command"], stdout=log, stderr=subprocess.STDOUT,
                                  start_new_session=True) as process:
                try:
                    record["exit_code"] = process.wait(timeout=timeout)
                except (subprocess.TimeoutExpired, KeyboardInterrupt):
                    # 仅终止本次启动的进程组，避免 profiler 子进程在超时后继续运行。
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    record["exit_code"] = 124
                    raise
        except OSError as error:
            log.write(str(error) + "\n")
            record["exit_code"] = 127
        finally:
            record["elapsed_seconds"] = time.monotonic() - started
            (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if required and record["exit_code"]:
        raise RuntimeError(f"{name} 失败，见 {record['log']}")
    return (output / record["log"]).read_text(errors="replace")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=int, required=True, help="ACL 逻辑设备号；先查看 npu-smi")
    parser.add_argument("--steps", type=int, default=4096)
    parser.add_argument("--output", type=Path, required=True, help="必须是新目录")
    parser.add_argument("--profile", action="store_true", help="基线通过后采集 PcSampling")
    parser.add_argument("--timeout", type=int, default=180, help="每条命令的超时秒数")
    args = parser.parse_args()
    if not (0 <= args.device <= 2147483647 and 1 <= args.steps <= 1048576 and args.timeout > 0):
        parser.error("device、steps 或 timeout 超出范围")
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    archive = Path(str(output) + ".tar.gz")
    if archive.exists():
        parser.error("回传包已存在，请使用新 output")
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    manifest = {"schema": "akl.simt_probe.v1", "status": "incomplete", "commands": [],
                "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "device": args.device, "steps": args.steps, "blocks": 1, "threads": 32,
                "table_words": 4096, "profile_requested": args.profile,
                "environment": {k: os.environ.get(k) for k in
                                ("ASCEND_HOME_PATH", "ASCEND_RT_VISIBLE_DEVICES")}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    try:
        def run(cmd, name, required=True):
            return execute(cmd, output, name, manifest, args.timeout, required)
        print(f"执行范围：逻辑设备 {args.device}，1 AIV × 32 线程，steps={args.steps}；profile={args.profile}", flush=True)
        run(["git", "-C", root, "rev-parse", "HEAD"], "revision", False)
        run(["git", "-C", root, "status", "--short"], "worktree", False)
        run(["npu-smi", "info"], "devices", False)
        cann = os.environ.get("ASCEND_HOME_PATH")
        if not cann:
            raise RuntimeError("未设置 ASCEND_HOME_PATH，请先 source CANN 环境")
        run([Path(cann) / "bin/bisheng", "--version"], "compiler-version")
        for label, path in (("cann-version", Path(cann) / "version.cfg"),
                            ("driver-version", Path("/usr/local/Ascend/driver/version.info"))):
            (output / (label + ".txt")).write_text(path.read_text() if path.is_file() else "unknown\n")
        source = output / "source"
        shutil.copytree(root / "examples/simt_source_probe", source)
        shutil.copy2(__file__, output / "run_simt_probe.py")
        manifest["source_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in source.iterdir() if p.is_file()}
        build = output / "build"
        run(["cmake", "-S", source, "-B", build], "configure")
        run(["cmake", "--build", build, "-j2"], "build")
        binary = build / "akl_simt_probe"
        manifest["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        app = [binary, str(args.device), str(args.steps)]
        run(app, "baseline")
        manifest["status"] = "baseline_passed"
        if args.profile:
            profiler = shutil.which("msopprof") or shutil.which("msprof")
            if not profiler:
                raise RuntimeError("找不到 msopprof/msprof")
            command = [profiler] + (["op"] if Path(profiler).name == "msprof" else [])
            run(command + ["--version"], "profiler-version", False)
            help_text = run(command + ["--help"], "profiler-help")
            if "PcSampling" not in help_text:
                raise RuntimeError("当前 profiler help 未声明 PcSampling；保留证据，不猜参数")
            run(command + ["--aic-metrics=PcSampling", "--kernel-name=akl_simt_probe_kernel",
                           "--launch-count=1", "--warm-up=0", "--output=" + str(output / "profile")]
                + app, "profile")
            reports = list((output / "profile").rglob("visualize_data.bin"))
            manifest["reports"] = [str(p.relative_to(output)) for p in reports if p.stat().st_size]
            if not manifest["reports"]:
                raise RuntimeError("采集命令结束但没有非空 visualize_data.bin")
            # 文件存在不证明有目标 VF 样本；仍需在 Insight 核对源码映射和采样覆盖。
            manifest["status"] = "captured_pending_review"
    except (Exception, KeyboardInterrupt) as error:
        manifest.update(status="failed", error=str(error) or "interrupted")
        print(manifest["error"], flush=True)
    finally:
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
        with archive.open("xb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as bundle:
            bundle.add(output, arcname="simt_probe", filter=lambda item:
                       item if item.isfile() or item.isdir() else None)
        print(f"{manifest['status']}；回传包：{archive}", flush=True)
    return int(manifest["status"] == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
