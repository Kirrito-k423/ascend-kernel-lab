"""离线解析 latency/旧 clock CSV；实验值为排除预热后的各 rank 均值的算术平均。"""
import argparse
import csv
import json
import math
from collections import defaultdict
from itertools import groupby
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
        work_fields = {'processed_bytes', 'sent_bytes'}
        if fieldnames & work_fields and not work_fields <= fieldnames:
            raise ValueError('both processed_bytes and sent_bytes are required')
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
            work = {name: int(item[name]) for name in work_fields} if work_fields <= fieldnames else {}
            if any(not 0 <= n <= 2**64-1 for n in work.values()):
                raise ValueError(f'{csv_path}:{line}: invalid uint64 work bytes')
            rows.append(dict(rank=rank, iteration=iteration, elapsed_us=us,
                             is_warmup=bool(warmup), in_average=bool(included),
                             measurement=item.get("measurement", "unspecified"), clock_hz=item.get("clock_hz", ""),
                             work_kind=item.get("work_kind", "unspecified"), **work))
    if not rows:
        raise ValueError(f"{csv_path} contains no latency rows")
    return rows


def plot_latency(rows: List[dict], csv_path: Path, output_path: Path, last_n: int = 5) -> Tuple[Dict[int, float], int]:
    if last_n < 0:
        raise ValueError('last_n must be nonnegative; 0 selects all measured samples')
    clocks = {(r.get("measurement", "unspecified"), r.get("clock_hz", "")) for r in rows}
    if len(clocks) != 1:
        raise ValueError("mixed latency measurement modes or clock frequencies")
    mode, hz = next(iter(clocks))
    boundary = {"kernel": f"max per-AIV / outer barriers excluded / SYS_CNT {hz} Hz",
                "event": "whole kernel / outer barriers included"}.get(mode, "unspecified legacy boundary")
    rows_by_rank = defaultdict(list)
    for row in rows:
        rows_by_rank[row['rank']].append(row)
    has_work = any('processed_bytes' in r or 'sent_bytes' in r for r in rows)
    if has_work and (not all('processed_bytes' in r and 'sent_bytes' in r for r in rows) or
                     len({r.get('work_kind', 'unspecified') for r in rows}) != 1):
        raise ValueError('mixed or missing work byte definitions')
    rank_means, counts, selected = {}, {}, {}
    for rank, samples in rows_by_rank.items():
        chosen = [r for r in sorted(samples, key=lambda r: r['iteration']) if r['in_average'] and not r['is_warmup']]
        selected[rank] = chosen[-last_n:] if last_n else chosen
        measured = [r['elapsed_us'] for r in selected[rank]]
        if not measured:
            raise ValueError(f"Rank {rank} has no measured samples")
        rank_means[rank], counts[rank] = mean(measured), len(measured)
    # 各 rank 等权；样本数不等时也不能改成把所有样本混合后的加权平均。
    experiment = mean(rank_means.values())
    fastest, slowest = min(rank_means.values()), max(rank_means.values())
    fast = [r for r in sorted(rank_means) if rank_means[r] == fastest]
    slow = [r for r in sorted(rank_means) if rank_means[r] == slowest]
    summary = dict(experiment_us=experiment, definition='unweighted mean of rank means over selected non-warmup samples', last_n=last_n,
                   rank_means_us=rank_means, measured_counts=counts,
                   fastest=dict(ranks=fast, mean_us=fastest), slowest=dict(ranks=slow, mean_us=slowest))
    by_launch = defaultdict(list)
    selected_pairs = {(rank, r['iteration']) for rank, chosen in selected.items() for r in chosen}
    for row in rows:
        by_launch[row['iteration']].append(row)
    launch_stats = {}
    for iteration, samples in sorted(by_launch.items()):
        low, high = min(r['elapsed_us'] for r in samples), max(r['elapsed_us'] for r in samples)
        launch_stats[iteration] = dict(min_us=low, max_us=high, mean_us=mean(r['elapsed_us'] for r in samples),
            min_ranks=sorted(r['rank'] for r in samples if r['elapsed_us']==low),
            max_ranks=sorted(r['rank'] for r in samples if r['elapsed_us']==high), ranks_present=len(samples),
            warmup=any(r['is_warmup'] for r in samples),
            selected=len(samples)==len(rows_by_rank) and all((r['rank'],iteration) in selected_pairs for r in samples))
    # 先逐轮取所有 rank 的最大值，再跨轮平均；只纳入各 rank 均选中的完整轮次。
    maxima = [v['max_us'] for v in launch_stats.values() if v['selected']]
    summary['launches'] = launch_stats
    summary['mean_launch_max_us'] = mean(maxima) if maxima else None
    summary['complete_selected_launches'] = len(maxima)
    rates = {}
    if has_work:
        # 变长工作量取总字节/总时间，不能平均每轮 GB/s；实验值仍按 rank 等权。
        for name in ('processed', 'sent'):
            rates[name] = {rank: sum(r[name+'_bytes'] for r in chosen) / (sum(r['elapsed_us'] for r in chosen) * 1000)
                           if sum(r['elapsed_us'] for r in chosen) > 0 else None for rank, chosen in selected.items()}
        summary['throughput'] = dict(unit='GB/s (10^9 bytes/s)', definition='per-rank sum(bytes)/sum(time); mean across ranks',
            work_kind=rows[0].get('work_kind', 'unspecified'), rank_gbps=rates,
            mean_gbps={name: mean(values.values()) if all(v is not None for v in values.values()) else None
                       for name, values in rates.items()})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.with_name(output_path.stem + '_summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    ranks = sorted(rows_by_rank)
    max_us = max(r['elapsed_us'] for r in rows)
    heat = matplotlib.colors.LinearSegmentedColormap.from_list('latency', ['#bfe5ca', '#fff5cc', '#f1b7b4'])
    table_ranges = {}
    for name in ('min', 'max', 'mean'):
        values = [v[name+'_us'] for v in launch_stats.values() if not v['warmup']]
        table_ranges[name] = min(values), max(values)
    pages = [(offset, start) for offset in range(0, len(ranks), 128) for start in range(0, len(launch_stats), 20)]
    for page, (offset, launch_start) in enumerate(pages, 1):
        page_ranks = ranks[offset:offset+128]
        iterations = list(launch_stats)[launch_start:launch_start+20]
        positions = {iteration: index for index, iteration in enumerate(iterations)}
        # 独立图例区域每行8项、每页最多128个rank，避免图例遮挡或无限拉长图片。
        legend_height = 0.24 * math.ceil((len(page_ranks)+3) / 8) + 0.2
        figure = plt.figure(figsize=(13, 8.0 + legend_height), layout='constrained')
        grid = figure.add_gridspec(4, 1, height_ratios=[0.6, 5.5, 1.1, legend_height])
        metrics, axis, table_axis, legends = [figure.add_subplot(grid[i]) for i in range(4)]
        table_axis.axis('off')
        metrics.axis('off')
        legends.axis('off')
        warmup_iterations = [positions[i] for i in iterations if launch_stats[i]['warmup']]
        # 按实际标记合并相邻轮次；每轮占 iteration ± 0.5，不覆盖正式测量。
        for index, (_, group) in enumerate(groupby(enumerate(warmup_iterations), lambda pair: pair[1]-pair[0])):
            warmup_group = [iteration for _, iteration in group]
            axis.axvspan(warmup_group[0]-0.5, warmup_group[-1]+0.5, color='#9ca3af', alpha=0.3, linewidth=0,
                         zorder=0, label='Warmup (gray / x)' if index == 0 else None, gid=f'warmup-{index}')
        def rank_label(ids):
            return f"rank {ids[0]}" + (f" (+{len(ids)-1} tied)" if len(ids)>1 else '')
        for x, label, value, color in [(0.12, 'Fastest rank mean / '+rank_label(fast), fastest, '#b91c1c'),
                                      (0.37, 'Mean of rank means', experiment, '#7c3aed'),
                                      (0.62, 'Slowest rank mean / '+rank_label(slow), slowest, '#b91c1c'),
                                      (0.87, f'Mean of launch maxima / {len(maxima)} launches', summary['mean_launch_max_us'], '#0369a1')]:
            number = f'{value:.3f} us' if value is not None else 'N/A'
            metrics.text(x, 0.5, f"{label}\n{number}", ha='center', va='center', color=color, fontsize=9)
        for i, rank in enumerate(page_ranks, offset):
            samples = sorted((r for r in rows_by_rank[rank] if r['iteration'] in positions), key=lambda r: r['iteration'])
            color = matplotlib.colors.hsv_to_rgb(((i * 0.61803398875) % 1, 0.7, 0.75))
            axis.scatter([positions[r['iteration']] for r in samples], [r['elapsed_us'] for r in samples],
                         s=18, alpha=0.7, color=color, label=f'Rank {rank}', zorder=3)
            extreme = rank_means[rank] in (fastest, slowest)
            axis.axhline(rank_means[rank], color='#b91c1c' if extreme else 'red', linestyle='--',
                         linewidth=1.2 if extreme else 0.8, alpha=1 if extreme else 0.55, gid=f'rank-mean-{rank}')
            warmup = [r for r in samples if r['is_warmup']]
            axis.scatter([positions[r['iteration']] for r in warmup], [r['elapsed_us'] for r in warmup],
                         marker='x', s=35, color=color, zorder=4)
        axis.axhline(experiment, color='#7c3aed', linewidth=2, label='Experiment mean', zorder=5)
        if maxima:
            axis.axhline(summary['mean_launch_max_us'], color='#0369a1', linewidth=2, linestyle='-.',
                label='Mean of launch maxima', gid='mean-launch-max')
        axis.set(xlabel='Launch / iteration', ylabel='Latency (us)', xlim=(-0.5, len(iterations)-0.5),
                 ylim=(0, max(1, max_us*1.08)))
        axis.set_xticks(range(len(iterations)), iterations)
        def cell(iteration, name):
            value = launch_stats[iteration]
            ids = value.get(name+'_ranks', [])
            rank = f'\nr{ids[0]}' + ('+' if len(ids)>1 else '') if ids else ''
            return f'{value[name+"_us"]:.2f}'+rank
        table = table_axis.table(cellText=[[cell(i, name) for i in iterations] for name in ('min','max','mean')],
            rowLabels=['Min us / rank','Max us / rank','Mean us'],
            colLabels=[str(i)+(' *' if launch_stats[i]['ranks_present']<len(ranks) else '') for i in iterations],
            cellLoc='center', bbox=[0,0,1,1])
        table.auto_set_font_size(False); table.set_fontsize(7)
        for column, iteration in enumerate(iterations):
            value = launch_stats[iteration]
            table[0,column].set_facecolor('#e5e7eb' if value['warmup'] else 'white')
            for row, name in enumerate(('min', 'max', 'mean'), 1):
                low, high = table_ranges[name]
                fraction = (value[name+'_us']-low)/(high-low) if high>low else .5
                table[row,column].set_facecolor('#e5e7eb' if value['warmup'] else heat(fraction))
        table_axis.set_title('Each row: green = low, red = high; gray = warmup; + = tied ranks; * = incomplete coverage', fontsize=8)
        axis.grid(True, linestyle=':', linewidth=0.6, alpha=0.4)
        legends.legend(*axis.get_legend_handles_labels(), loc='center', ncol=8,
                       fontsize=8, frameon=False, columnspacing=1.2, handletextpad=0.3)
        window = f"last {last_n} measured/rank" if last_n else "all measured samples"
        figure.suptitle(f"Latency / {csv_path.parent.name} / page {page}/{len(pages)} / experiment: all {len(ranks)} ranks / {window}\n{boundary}")
        target = output_path if page == 1 else output_path.with_name(f'{output_path.stem}_page{page}{output_path.suffix}')
        figure.savefig(target, dpi=160)
        figure.savefig(target.with_suffix('.svg'))
        plt.close(figure)
    _plot_launch_latency(launch_stats, len(ranks), csv_path, output_path, boundary)
    if has_work:
        _plot_throughput(rows_by_rank, summary['throughput'], csv_path, output_path, boundary, window)
    print(f'Experiment: {experiment:.6f} us; fastest: {fastest:.6f} us; slowest: {slowest:.6f} us')
    return rank_means, sum(counts.values())


def _plot_launch_latency(launches, rank_count, csv_path, latency_path, boundary):
    figure, axis = plt.subplots(figsize=(13, 5.5))
    iterations = list(launches)
    partial = [i for i in iterations if launches[i]['ranks_present'] < rank_count]
    for name, title, color, marker, style in [('max', 'Slowest', '#c2410c', '^', '-'),
            ('mean', 'Mean', '#2563eb', 'o', '--'), ('min', 'Fastest', '#0f766e', 's', ':')]:
        axis.plot(iterations, [launches[i][name+'_us'] for i in iterations], label=title,
                  color=color, marker=marker, linestyle=style, markersize=5, gid='launch-'+name)
        if partial:
            axis.scatter(partial, [launches[i][name+'_us'] for i in partial], color='black', marker='x',
                         s=60, zorder=4, label='Incomplete rank coverage' if name=='max' else None)
    for index, iteration in enumerate(i for i in iterations if launches[i]['warmup']):
        axis.axvspan(iteration-.5, iteration+.5, color='#9ca3af', alpha=.3, linewidth=0,
                     label='Warmup' if index==0 else None, gid='launch-warmup-'+str(index))
    axis.set(xlabel='Launch / iteration', ylabel='Latency (us)', ylim=(0, None),
             xlim=(iterations[0]-.5, iterations[-1]+.5),
             title=f'Per-launch latency / {csv_path.parent.name} / {rank_count} ranks (available samples per launch)\n{boundary}')
    valid = [i for i in iterations if launches[i]['selected']]
    if valid:
        low, high = min(launches[i]['min_us'] for i in valid), max(launches[i]['max_us'] for i in valid)
        padding = (high-low)*.25 if high>low else max(abs(low)*.02, .001)
        axis.set_ylim(max(0, low-padding), high+padding)
        axis.set_title(axis.get_title()+'\nY range: selected launches; out-of-range warmup/unselected points are clipped', fontsize=10)
        # 与跨轮均值使用同一有效集合；并列极值标最早一轮，原始 rank 列表保留在 JSON。
        for name, choose, label, color, offset in [('max', max, 'Slowest max', '#c2410c', .15),
                ('max', min, 'Slowest min', '#c2410c', -.06),
                ('min', max, 'Fastest max', '#0f766e', .15),
                ('min', min, 'Fastest min', '#0f766e', -.06)]:
            iteration = choose(valid, key=lambda i: launches[i][name+'_us'])
            value, ids = launches[iteration][name+'_us'], launches[iteration][name+'_ranks']
            key = name+'-'+choose.__name__
            axis.scatter([iteration], [value], s=180, marker='*' if choose is max else 'D', color=color, edgecolors='black',
                         linewidths=.8, zorder=6, gid='selected-'+key)
            note = f'{label}: {value:.3f} us\nLaunch {iteration} / rank {ids[0]}' + (' (+ties)' if len(ids)>1 else '')
            x = (iteration-iterations[0]+.5) / (iterations[-1]-iterations[0]+1)
            bottom, top = axis.get_ylim()
            y = min(.98, max(.14, (value-bottom)/(top-bottom)+offset))
            axis.annotate(note, xy=(iteration, value), xytext=(x,y), textcoords='axes fraction',
                          ha='left' if x<.5 else 'right', va='top', color=color, fontsize=10, zorder=7,
                          bbox=dict(boxstyle='round,pad=.4', fc='white', ec=color),
                          arrowprops=dict(arrowstyle='->', color=color), gid='selected-'+key+'-label')
    if len(iterations) <= 30:
        axis.set_xticks(iterations)
    else:
        axis.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    axis.grid(True, linestyle=':', alpha=.4)
    figure.legend(*axis.get_legend_handles_labels(), loc='lower center', ncol=5, frameon=False)
    figure.tight_layout(rect=(0, .07, 1, 1))
    target = latency_path.with_name('dispatch_latency_launches'+latency_path.suffix)
    figure.savefig(target, dpi=160)
    figure.savefig(target.with_suffix('.svg'))
    plt.close(figure)


def _plot_throughput(rows_by_rank, summary, csv_path, latency_path, boundary, window):
    ranks = sorted(rows_by_rank)
    for offset in range(0, len(ranks), 128):
        page_ranks = ranks[offset:offset+128]
        legend_height = 0.24 * math.ceil(len(page_ranks)/8) + 0.2
        figure = plt.figure(figsize=(13, 7 + legend_height), layout='constrained')
        grid = figure.add_gridspec(3, 1, height_ratios=[3, 3, legend_height])
        axes = [figure.add_subplot(grid[i]) for i in range(3)]
        for axis, name, title in zip(axes, ('processed', 'sent'), ('Processed payload', 'Sent payload')):
            for index, rank in enumerate(page_ranks, offset):
                samples = sorted(rows_by_rank[rank], key=lambda r:r['iteration'])
                timed = [r for r in samples if r['elapsed_us']>0]
                color = matplotlib.colors.hsv_to_rgb(((index*0.61803398875)%1,0.7,0.75))
                axis.scatter([r['iteration'] for r in timed], [r[name+'_bytes']/(r['elapsed_us']*1000) for r in timed],
                    s=18, alpha=0.7, color=color, label=f'Rank {rank}')
            for iteration in sorted({r['iteration'] for rank in page_ranks for r in rows_by_rank[rank] if r['is_warmup']}):
                axis.axvspan(iteration-.5,iteration+.5,color='#9ca3af',alpha=.3,linewidth=0)
            value = summary['mean_gbps'][name]
            if value is not None:
                axis.axhline(value,color='#7c3aed',linewidth=2,gid=f'{name}-mean-gbps')
            axis.set(title=f'{title} / mean: {value:.3f} GB/s' if value is not None else title+' / mean: N/A',
                xlabel='Launch / iteration',ylabel='GB/s (10^9 bytes/s)',ylim=(0,None))
            axis.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
            axis.grid(True,linestyle=':',alpha=.4)
        axes[2].axis('off')
        axes[2].legend(*axes[0].get_legend_handles_labels(),loc='center',ncol=8,fontsize=8,frameon=False)
        figure.suptitle(f'Payload throughput / {csv_path.parent.name} / {summary["work_kind"]} / {window} / gray = warmup\n{boundary}\n{summary["definition"]}', fontsize=11)
        suffix = '' if not offset else f'_page{offset//128+1}'
        target = latency_path.with_name('dispatch_bandwidth'+suffix+latency_path.suffix)
        figure.savefig(target,dpi=160);figure.savefig(target.with_suffix('.svg'));plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze dispatch clock and latency CSV files.")
    parser.add_argument(
        "input_dir",
        nargs="?",
        default=".",
        help="Run directory containing dispatch_clock/ or a directory containing the CSV files directly.",
    )
    parser.add_argument("--file-prefix", default=DEFAULT_FILE_PREFIX, help="Clock CSV file prefix.")
    parser.add_argument("--slots", type=int, default=20, help="Number of time columns to analyze.")
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
    parser.add_argument("--last-n", type=int, default=5, help="Average the last N non-warmup samples per rank; 0 uses all (default: 5).")
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
    if args.last_n < 0:
        raise ValueError('last-n must be nonnegative')
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
                latency_rows, latency_csv, latency_plot, last_n=args.last_n
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
