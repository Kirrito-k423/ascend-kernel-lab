"""默认编译、独立运行 ON/OFF 两个版本，校验已加载的开关并生成成对延迟图。"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
from statistics import mean
import subprocess
import time


def command(text, cwd, env, log, timeout):
    # 独立进程组：中断只清理本次命令的子进程，不触碰其他实验。
    with log.open('w') as stream:
        process = subprocess.Popen(['bash', '-euo', 'pipefail', '-c', text], cwd=cwd,
                                   env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
            if code:
                raise RuntimeError(f'command exited {code}; see {log}')
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise


def run(args):
    from .latency_plot import load_latency_rows, plot_latency
    cwd, output = Path(args.cwd).resolve(), Path(args.output).resolve()
    if not cwd.is_dir() or args.last_n < 0 or (args.timeout is not None and args.timeout <= 0):
        raise ValueError('cwd must exist; last-n >= 0; timeout > 0')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', args.build_flag):
        raise ValueError('build-flag must be an environment variable name')
    if args.build_flag.startswith('AKL_') or args.build_flag in ('PATH', 'HOME', 'BUILD_DIR', 'PYTHONPATH'):
        raise ValueError('build-flag conflicts with a reserved environment variable')
    if Path(args.csv_name).name != args.csv_name or args.csv_name in ('', '.', '..'):
        raise ValueError('csv-name must be a filename')
    output.mkdir(parents=True, exist_ok=False)
    manifest = dict(schema='akl.latency-comparison.v1', status='running', cwd=str(cwd),
                    started_utc=datetime.now(timezone.utc).isoformat(), build=args.build, run=args.run,
                    build_flag=args.build_flag, last_n=args.last_n, variants={})
    def save():
        temporary = output / 'comparison.tmp'
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False))
        temporary.replace(output / 'comparison.json')
    save()
    rows_by_mode, keys = {}, None
    try:
        # ON 后 OFF，正常完成后使用仓保留无打点构建；两次运行均为新进程。
        for mode in ('ON', 'OFF'):
            folder = output / ('trace_' + mode.lower())
            folder.mkdir()
            env = dict(os.environ, AKL_TRACE_MODE=mode, AKL_LATENCY_OUTPUT_DIR=str(folder),
                       AKL_TRACE_DIR=str(folder / 'clock'), BUILD_DIR=str(folder / 'build'))
            env[args.build_flag] = mode
            state = manifest['variants'][mode] = dict(status='building', directory=folder.name)
            for stage, script in (('build', args.build), ('run', args.run)):
                state['status'] = stage
                save()
                print(f'[{mode}] {stage}: {folder / (stage + ".log")}', flush=True)
                start = time.monotonic()
                command(script, cwd, env, folder / (stage + '.log'), args.timeout)
                state[stage + '_seconds'] = time.monotonic() - start
            csv_path = folder / args.csv_name
            with csv_path.open(newline='') as stream:
                recorded_modes = {row.get('trace_mode') for row in csv.DictReader(stream)}
            if recorded_modes != {mode}:
                raise ValueError(f'{csv_path}: expected loaded trace_mode={mode}, got {recorded_modes}')
            rows = rows_by_mode[mode] = load_latency_rows(csv_path)
            sample_keys = {(r['rank'], r['iteration'], r['is_warmup'], r['in_average']) for r in rows}
            if keys is not None and keys != sample_keys:
                raise ValueError('ON/OFF rank, iteration or warmup/average masks differ; comparison refused')
            keys = sample_keys
            state.update(status='collected', csv=str(csv_path.relative_to(output)),
                         csv_sha256=hashlib.sha256(csv_path.read_bytes()).hexdigest(), samples=len(rows))
            save()
        # 同一纵轴、同一统计窗口；保留全部原始样本和每 rank 均值。
        y_max = max(r['elapsed_us'] for rows in rows_by_mode.values() for r in rows)
        for mode, rows in rows_by_mode.items():
            state = manifest['variants'][mode]
            plot = output / f'latency_{mode.lower()}.png'
            means, count = plot_latency(rows, output / state['csv'], plot, args.last_n,
                                        title=f'DebugClock {mode}', y_max=y_max)
            state.update(status='complete', plot=plot.name, experiment_us=mean(means.values()),
                         rank_means_us=means, measured_samples=count)
        on, off = [manifest['variants'][mode]['experiment_us'] for mode in ('ON', 'OFF')]
        manifest.update(status='complete', delta_us=on-off,
                        overhead_percent=(on-off)/off*100 if off else None,
                        definition='ON minus OFF; each experiment is the unweighted mean of rank means',
                        caveat='Sequential independent runs; difference includes run-to-run variation. Timing scope is defined by the backend, not build/run wall time.')
        save()
        print(f'ON={on:.6f} us; OFF={off:.6f} us; delta={on-off:.6f} us; {output / "comparison.json"}')
    except BaseException as error:
        manifest.update(status='failed', error=f'{type(error).__name__}: {error}')
        save()
        raise
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True, help='Build/install command, run twice with ON/OFF environment')
    parser.add_argument('--run', required=True, help='Same foreground job command for both versions; includes LatencyProfile')
    parser.add_argument('--cwd', default='.', help='Consumer repository working directory')
    parser.add_argument('--output', default='results/latency-pair/' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    parser.add_argument('--build-flag', default='DEBUG_CLOCK_ON', help='Build environment variable, default DEBUG_CLOCK_ON')
    parser.add_argument('--csv-name', default='dispatch_latency.csv')
    parser.add_argument('--last-n', type=int, default=5, help='Last N measured samples per rank; 0 uses all')
    parser.add_argument('--timeout', type=float, help='Maximum seconds per build/run command')
    run(parser.parse_args())


if __name__ == '__main__':
    main()
