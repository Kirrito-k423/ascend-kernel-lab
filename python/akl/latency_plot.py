"""离线解析 latency/旧 clock CSV；实验值为排除预热后的各 rank 均值的算术平均。"""
import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Dict
from typing import List, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


DEFAULT_PHASE_NAMES = []

DEFAULT_FILE_PREFIX = "dispatch_clock"
DEFAULT_LATENCY_FILE = "dispatch_latency.csv"


def find_input_files(input_dir: Path, file_prefix: str) -> List[Path]:
    if not input_dir.exists():
        raise FileNotFoundError(f"input directory does not exist: {input_dir}")
    if not input_dir.is_dir():
        raise NotADirectoryError(f"input path is not a directory: {input_dir}")

    patterns = [
        f"{file_prefix}_rank_*.csv",
        f"{file_prefix}_*_rank_*.csv",
    ]

    def raw_clock_files(paths) -> List[Path]:
        return sorted(path for path in paths if not path.name.endswith("_summary.csv"))

    for pattern in patterns:
        paths = raw_clock_files(input_dir.glob(pattern))
        if paths:
            return paths

    # The normal layout puts clock CSV files in <run_dir>/dispatch_clock/.
    for pattern in patterns:
        paths = raw_clock_files(input_dir.rglob(pattern))
        if paths:
            return paths
    return []


def time_col_index(name: str) -> int:
    # Expected format: time_0_cycles
    return int(name.split("_")[1])


def load_clock_csv(path: Path) -> Tuple[int, np.ndarray, List[int], List[str]]:
    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"{path} has no CSV header")

        time_cols = [
            name for name in reader.fieldnames
            if name.startswith("time_") and name.endswith("_cycles")
        ]
        time_cols = sorted(time_cols, key=time_col_index)
        if not time_cols:
            raise ValueError(f"{path} has no time_*_cycles columns")

        rows = []
        for row in reader:
            rank = int(row["rank"])
            aiv_id = int(row["aiv_id"])
            values = [int(row[col]) for col in time_cols]
            rows.append((rank, aiv_id, values))

    if not rows:
        raise ValueError(f"{path} has no data rows")

    ranks = {row[0] for row in rows}
    if len(ranks) != 1:
        raise ValueError(f"{path} contains multiple ranks: {sorted(ranks)}")

    rows.sort(key=lambda item: item[1])
    rank = rows[0][0]
    aiv_ids = [row[1] for row in rows]
    data = np.array([row[2] for row in rows], dtype=np.int64)
    return rank, data, aiv_ids, time_cols


def make_phase_names(raw_names: List[str], valid_slots: int, custom_names: List[str]) -> List[str]:
    names: List[str] = []
    for i in range(valid_slots):
        if i < len(custom_names):
            names.append(custom_names[i])
        elif i < len(DEFAULT_PHASE_NAMES):
            names.append(DEFAULT_PHASE_NAMES[i])
        else:
            names.append(raw_names[i].replace("_cycles", ""))
    return names


def sanitize_cycles(data: np.ndarray, source: Path) -> np.ndarray:
    if np.any(data < 0):
        raise ValueError(f"{source}: negative cycle values; refusing to replace invalid samples with zero")
    return data


def write_summary_csv(
    output_path: Path,
    rank: int,
    aiv_ids: List[int],
    data_us: np.ndarray,
    phase_names: List[str],
) -> None:
    with output_path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rank", "aiv_id", *[f"{name}_us" for name in phase_names], "total_us"])
        for aiv_id, row in zip(aiv_ids, data_us):
            writer.writerow([rank, aiv_id, *[f"{value:.6f}" for value in row], f"{np.sum(row):.6f}"])


def print_stats(rank: int, aiv_ids: List[int], data_us: np.ndarray, phase_names: List[str]) -> None:
    print(f"\nrank {rank}")
    print("phase, mean_us, p50_us, p95_us, max_us")
    for idx, name in enumerate(phase_names):
        col = data_us[:, idx]
        print(
            f"{name}, "
            f"{np.mean(col):.3f}, "
            f"{np.percentile(col, 50):.3f}, "
            f"{np.percentile(col, 95):.3f}, "
            f"{np.max(col):.3f}"
        )
    total = np.sum(data_us, axis=1)
    slowest_idx = int(np.argmax(total))
    slowest_aiv = aiv_ids[slowest_idx]
    print(
        f"total, mean={np.mean(total):.3f} us, "
        f"p95={np.percentile(total, 95):.3f} us, "
        f"max={np.max(total):.3f} us at aiv {slowest_aiv}"
    )


def plot_rank(
    output_path: Path,
    rank: int,
    aiv_ids: List[int],
    data_us: np.ndarray,
    phase_names: List[str],
    title: str,
) -> None:
    fig_height = max(7.0, len(aiv_ids) * 0.22)
    fig, ax = plt.subplots(figsize=(15, fig_height))

    y_pos = np.arange(len(aiv_ids))
    left = np.zeros(len(aiv_ids), dtype=np.float64)
    colors = plt.cm.tab20(np.linspace(0, 1, max(len(phase_names), 2)))

    for idx, phase in enumerate(phase_names):
        ax.barh(
            y_pos,
            data_us[:, idx],
            left=left,
            height=0.78,
            color=colors[idx],
            label=phase,
        )
        left += data_us[:, idx]

    ax.set_yticks(y_pos)
    ax.set_yticklabels([f"aiv{aiv_id}" for aiv_id in aiv_ids])
    ax.invert_yaxis()
    ax.set_xlabel("time (us)")
    ax.set_title(title or f"dispatch clock rank {rank}")
    ax.grid(axis="x", linestyle="--", linewidth=0.5, alpha=0.35)
    legend_rows = math.ceil(len(phase_names) / 6)
    legend_height = 0.23 * legend_rows + 0.5
    fig.set_size_inches(15, fig_height + legend_height)
    fig.legend(*ax.get_legend_handles_labels(), loc="lower center", ncol=6,
               fontsize=9, frameon=False, bbox_to_anchor=(0.5, 0.01))
    fig.tight_layout(rect=(0, legend_height / (fig_height + legend_height), 1, 1))
    fig.savefig(output_path, dpi=160)
    plt.close(fig)


def find_latency_csv(input_dir: Path, file_name: str) -> Path:
    direct_path = input_dir / file_name
    if direct_path.is_file():
        return direct_path

    legacy_matches = sorted(input_dir.glob("dispatch_latency_*.csv"))
    if not legacy_matches:
        legacy_matches = sorted(input_dir.rglob(file_name))
    if not legacy_matches:
        legacy_matches = sorted(input_dir.rglob("dispatch_latency_*.csv"))
    if not legacy_matches:
        raise FileNotFoundError(f"No latency CSV found under {input_dir}")
    if len(legacy_matches) > 1:
        raise ValueError(f"Found {len(legacy_matches)} latency CSVs; select one experiment directory or CSV file")
    return legacy_matches[0]


def load_latency_rows(csv_path: Path) -> List[dict]:
    required_columns = {"rank", "iteration", "elapsed_us"}
    rows: List[dict] = []
    with csv_path.open("r", newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fieldnames = set(reader.fieldnames or [])
        missing = required_columns - fieldnames
        if missing:
            raise ValueError(
                f"{csv_path} is missing columns: {', '.join(sorted(missing))}"
            )
        seen = set()
        for line, item in enumerate(reader, 2):
            rank, iteration, us = int(item['rank']), int(item['iteration']), float(item['elapsed_us'])
            warmup = int(item.get('is_warmup', '0') or '0')
            included = int(item.get('in_average', str(1-warmup)) or str(1-warmup))
            if rank < 0 or iteration < 0 or not math.isfinite(us) or us < 0:
                raise ValueError(f"{csv_path}:{line}: invalid rank/iteration/latency")
            if warmup not in (0, 1) or included not in (0, 1) or (warmup and included):
                raise ValueError(f"{csv_path}:{line}: invalid warmup/in_average flags")
            if (rank, iteration) in seen:
                raise ValueError(f"{csv_path}:{line}: duplicate rank/iteration")
            seen.add((rank, iteration))
            rows.append(dict(rank=rank, iteration=iteration, elapsed_us=us,
                             is_warmup=bool(warmup), in_average=bool(included)))
    if not rows:
        raise ValueError(f"{csv_path} contains no latency rows")
    return rows


def plot_latency(rows: List[dict], csv_path: Path, output_path: Path) -> Tuple[Dict[int, float], int]:
    rows_by_rank = defaultdict(list)
    for row in rows:
        rows_by_rank[row['rank']].append(row)
    rank_means, counts = {}, {}
    for rank, samples in rows_by_rank.items():
        measured = [r['elapsed_us'] for r in samples if r['in_average'] and not r['is_warmup']]
        if not measured:
            raise ValueError(f"Rank {rank} has no measured samples")
        rank_means[rank], counts[rank] = mean(measured), len(measured)
    # 各 rank 等权；样本数不等时也不能改成把所有样本混合后的加权平均。
    experiment = mean(rank_means.values())
    fastest, slowest = min(rank_means.values()), max(rank_means.values())
    fast = [r for r in sorted(rank_means) if rank_means[r] == fastest]
    slow = [r for r in sorted(rank_means) if rank_means[r] == slowest]
    summary = dict(experiment_us=experiment, definition='unweighted mean of measured rank means',
                   rank_means_us=rank_means, measured_counts=counts,
                   fastest=dict(ranks=fast, mean_us=fastest), slowest=dict(ranks=slow, mean_us=slowest))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.with_name(output_path.stem + '_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    ranks = sorted(rows_by_rank)
    max_iteration = max(r['iteration'] for r in rows)
    max_us = max(r['elapsed_us'] for r in rows)
    for offset in range(0, len(ranks), 128):
        page_ranks = ranks[offset:offset+128]
        # 独立图例区域每行8项、每页最多128个rank，避免图例遮挡或无限拉长图片。
        legend_height = 0.24 * math.ceil((len(page_ranks)+2) / 8) + 0.2
        figure = plt.figure(figsize=(13, 6.8 + legend_height), layout='constrained')
        grid = figure.add_gridspec(3, 1, height_ratios=[0.6, 5.5, legend_height])
        metrics, axis, legends = [figure.add_subplot(grid[i]) for i in range(3)]
        metrics.axis('off')
        legends.axis('off')
        def rank_label(ids):
            return f"rank {ids[0]}" + (f" (+{len(ids)-1} tied)" if len(ids)>1 else '')
        for x, label, value, color in [(0.16, 'Fastest mean / '+rank_label(fast), fastest, '#b91c1c'),
                                      (0.5, 'Experiment / mean of rank means', experiment, '#7c3aed'),
                                      (0.84, 'Slowest mean / '+rank_label(slow), slowest, '#b91c1c')]:
            metrics.text(x, 0.5, f"{label}\n{value:.6f} us", ha='center', va='center', color=color, fontsize=10)
        for i, rank in enumerate(page_ranks, offset):
            samples = sorted(rows_by_rank[rank], key=lambda r: r['iteration'])
            color = matplotlib.colors.hsv_to_rgb(((i * 0.61803398875) % 1, 0.7, 0.75))
            axis.scatter([r['iteration'] for r in samples], [r['elapsed_us'] for r in samples],
                         s=18, alpha=0.7, color=color, label=f'Rank {rank}', zorder=3)
            axis.axhline(rank_means[rank], color='red', linestyle='--', linewidth=0.7, alpha=0.25)
            warmup = [r for r in samples if r['is_warmup']]
            axis.scatter([r['iteration'] for r in warmup], [r['elapsed_us'] for r in warmup],
                         marker='x', s=35, color=color, zorder=4)
        axis.axhline(fastest, color='#b91c1c', linestyle='--', linewidth=1.2)
        axis.axhline(slowest, color='#b91c1c', linestyle='--', linewidth=1.2)
        axis.axhline(experiment, color='#7c3aed', linewidth=2, label='Experiment mean', zorder=5)
        axis.scatter([], [], color='#64748b', marker='x', label='Warmup (excluded)')
        axis.set(xlabel='Iteration', ylabel='Latency (us)', xlim=(-0.5, max_iteration+0.5),
                 ylim=(0, max(1, max_us*1.08)))
        if max_iteration < 30:
            axis.set_xticks(range(max_iteration+1))
        axis.grid(True, linestyle=':', linewidth=0.6, alpha=0.4)
        legends.legend(*axis.get_legend_handles_labels(), loc='center', ncol=8,
                       fontsize=8, frameon=False, columnspacing=1.2, handletextpad=0.3)
        page = offset//128 + 1
        figure.suptitle(f"Latency / {csv_path.parent.name} / page {page}/{math.ceil(len(ranks)/128)} / experiment: all {len(ranks)} ranks")
        target = output_path if not offset else output_path.with_name(f'{output_path.stem}_page{page}{output_path.suffix}')
        figure.savefig(target, dpi=160)
        figure.savefig(target.with_suffix('.svg'))
        plt.close(figure)
    print(f'Experiment: {experiment:.6f} us; fastest: {fastest:.6f} us; slowest: {slowest:.6f} us')
    return rank_means, sum(counts.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze dispatch clock and latency CSV files.")
    parser.add_argument(
        "input_dir",
        nargs="?",
        default=".",
        help="Run directory containing dispatch_clock/ or a directory containing the CSV files directly.",
    )
    parser.add_argument("--file-prefix", default=DEFAULT_FILE_PREFIX, help="Clock CSV file prefix.")
    parser.add_argument("--slots", type=int, default=11, help="Number of time columns to analyze.")
    parser.add_argument("--cycle-us", type=float, default=0.001, help="Microseconds per cycle.")
    parser.add_argument(
        "--out-dir",
        default="",
        help="Directory for plots and summary CSV files. Default: <input_dir>/plots",
    )
    parser.add_argument(
        "--phase-names",
        nargs="*",
        default=[],
        help="Optional phase names for valid slots, for example: --phase-names send local_copy",
    )
    parser.add_argument("--title", default="", help="Optional plot title prefix.")
    parser.add_argument(
        "--latency-file",
        default=DEFAULT_LATENCY_FILE,
        help=f"Latency CSV filename (default: {DEFAULT_LATENCY_FILE}).",
    )
    parser.add_argument("--skip-clock", action="store_true", help="Skip clock CSV analysis.")
    parser.add_argument("--skip-latency", action="store_true", help="Skip latency CSV analysis.")
    args = parser.parse_args()

    input_dir = Path(args.input_dir).expanduser().resolve()
    if input_dir.is_file():
        args.latency_file, args.skip_clock = str(input_dir), True
        input_dir = input_dir.parent
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Input directory does not exist: {input_dir}")

    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else input_dir / "plots"
    if args.slots <= 0 or not math.isfinite(args.cycle_us) or args.cycle_us <= 0:
        raise ValueError('slots and cycle-us must be positive (cycle-us finite)')
    out_dir.mkdir(parents=True, exist_ok=True)
    analyzed_anything = False

    if not args.skip_clock:
        clock_paths = find_input_files(input_dir, args.file_prefix)
        if not clock_paths:
            print(f"[INFO] No clock CSV files found in {input_dir}; skipping clock plots")
        for path in clock_paths:
            rank, cycles, aiv_ids, raw_names = load_clock_csv(path)
            valid_slots = min(args.slots, cycles.shape[1])
            cycles = sanitize_cycles(cycles[:, :valid_slots], path)
            data_us = cycles.astype(np.float64) * args.cycle_us
            phase_names = make_phase_names(raw_names, valid_slots, args.phase_names)

            summary_path = out_dir / f"{path.stem}_summary.csv"
            plot_path = out_dir / f"{path.stem}.png"

            write_summary_csv(summary_path, rank, aiv_ids, data_us, phase_names)
            print_stats(rank, aiv_ids, data_us, phase_names)
            plot_title = f"{args.title} rank {rank}".strip() if args.title else ""
            plot_rank(plot_path, rank, aiv_ids, data_us, phase_names, plot_title)

            print(f"saved summary: {summary_path}")
            print(f"saved plot: {plot_path}")
            analyzed_anything = True

    latency_means: Dict[int, float] = {}
    latency_csv = None
    latency_plot = None
    measured_sample_count = 0
    if not args.skip_latency:
        try:
            latency_csv = find_latency_csv(input_dir, args.latency_file)
        except FileNotFoundError:
            print(f"[INFO] No latency CSV found in {input_dir}; skipping latency plot")
        else:
            latency_plot = out_dir / "dispatch_latency_scatter.png"
            latency_rows = load_latency_rows(latency_csv)
            latency_means, measured_sample_count = plot_latency(
                latency_rows, latency_csv, latency_plot
            )
            analyzed_anything = True

    if not analyzed_anything:
        raise SystemExit(f"No dispatch clock or latency CSV files found in {input_dir}")

    if latency_means:
        print("\nDispatch latency measured means (warmup excluded)")
        print(f"CSV: {latency_csv}")
        print(f"Measured samples: {measured_sample_count}")
        for rank in sorted(latency_means):
            print(f"Rank {rank}: {latency_means[rank]:.6f} us")
        print(f"saved latency plot: {latency_plot}")


if __name__ == "__main__":
    main()
