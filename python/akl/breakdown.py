"""各 rank 独立的耗时图；PNG 默认显示核均值，HTML 按需查看、导出单核。"""
import colorsys
import html
import io
import json
import math
import textwrap
from collections import defaultdict
from pathlib import Path

REPORTS = ('breakdown_duration.png', 'breakdown_share.png')


def aggregate(meta, events):
    paths = sorted({tuple(e['path']) for e in events})
    indices = {path: i for i, path in enumerate(paths)}
    lanes = defaultdict(list)
    for event in events:
        lanes[(event['block'], event['subblock'])].append(event)
    for block in range(meta['blocks']):
        if not any(b == block for b, _ in lanes):
            lanes[(block, '?')] = []
    cores = []
    for (block, subblock), lane in sorted(lanes.items()):
        totals = [0] * len(paths)
        for first, last in zip(lane, lane[1:]):
            # 先用整数相减，再做显示换算；重复路径相加，末点不虚构区间。
            totals[indices[tuple(first['path'])]] += int(last['tick']) - int(first['tick'])
        cores.append(dict(label=f'block {block}/{subblock}', totals=totals,
                          valid=len(lane) >= 2, divisor=1))
    valid = [core for core in cores if core['valid']]
    average = dict(label=f'Core mean ({len(valid)}/{len(cores)})', valid=bool(valid),
                   divisor=len(valid) or 1,
                   totals=[sum(c['totals'][i] for c in valid) for i in range(len(paths))])
    # 平均占比 = 类型总和 / 各核首末跨度总和；不是跨核并行 E2E，也不是百分比均值。
    return paths, [average, *cores]


def render_breakdown(folder, meta, events, warnings, clock_mhz):
    from matplotlib import rc_context
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.font_manager import fontManager

    paths, panels = aggregate(meta, events)
    colors = [colorsys.hls_to_rgb(i * 0.381966 % 1, 0.55, 0.65) for i in range(len(paths))]
    labels = ['\n'.join(textwrap.wrap(' / '.join(p), 36)) for p in paths]
    factor, unit = (1 / clock_mhz, 'us') if clock_mhz else (1, 'cycle')
    fonts = [n for n in ('Noto Sans CJK SC', 'PingFang SC', 'DejaVu Sans') if n in {f.name for f in fontManager.ttflist}]
    # SVG 保留文本而非字体轮廓，单核切换无需巨型拼图，也不重复导出大量 PNG。
    with rc_context({'font.family': fonts, 'svg.fonttype': 'none', 'text.parse_math': False}):
        for panel in panels:
            values = [v * factor / panel['divisor'] for v in panel['totals']]
            total = sum(values)
            active = sorted((i for i, v in enumerate(values) if v > 0), key=lambda i: (-values[i], paths[i]))
            height = max(5, sum(labels[i].count('\n') + 2 for i in active) * 0.16 + 1.6)
            panel['svg'] = []
            for kind, name in enumerate(REPORTS):
                fig = Figure(figsize=(12, height), dpi=100)
                FigureCanvasAgg(fig)
                ax = fig.add_axes([0.35 if kind == 0 else 0.03, 0.4/height,
                                   0.51 if kind == 0 else 0.94, (height-1.2)/height])
                fig.suptitle(f'Rank {meta["rank"]} | {panel["label"]} | '
                             + (f'observed E2E {total:.3g} {unit}' if panel['valid'] else 'E2E N/A'), fontsize=12, y=1-0.2/height)
                fig.text(0.5, 1-0.6/height, 'First-to-last retained marker; not full kernel latency. '
                         + ('WARNING: truncated prefixes.' if warnings else 'No dropped records.'), ha='center', fontsize=9)
                if not panel['valid'] or not total:
                    ax.text(0.5, 0.5, 'No measured interval' if not panel['valid'] else 'Zero span; share N/A',
                            transform=ax.transAxes, ha='center')
                    ax.set_axis_off()
                elif kind == 0:
                    bars = ax.barh(range(len(active)), [values[i] for i in active], color=[colors[i] for i in active])
                    ax.set_yticks(range(len(active)), [labels[i] for i in active], fontsize=9)
                    ax.bar_label(bars, labels=[f'{values[i]:.3g} {unit}' for i in active], padding=5, fontsize=9)
                    ax.invert_yaxis()
                    ax.set_xlabel(f'Total duration ({unit})')
                    ax.set_xlim(0, max(values) * 1.2)
                else:
                    wedges, _ = ax.pie([values[i] for i in active], colors=[colors[i] for i in active], startangle=90, radius=1.35)
                    anchors = [(1.35*math.cos(math.radians((w.theta1+w.theta2)/2)),
                                1.35*math.sin(math.radians((w.theta1+w.theta2)/2)), i) for w, i in zip(wedges, active)]
                    half_height = max(1.45, (height-1.2) * 6.6 / (12 * 0.94) / 2)
                    # 左右两侧按圆周顺序放标签，均匀排开；引导线先水平离开圆周，再折向文字，避免穿过扇区。
                    for side in (-1, 1):
                        group = sorted((a for a in anchors if (a[0] < 0) == (side < 0)), key=lambda a: a[1])
                        for index, (x, y, i) in enumerate(group):
                            label_y = y if len(group) == 1 else (2 * index / (len(group)-1) - 1) * (half_height - 0.35)
                            ax.plot([x, side*1.45, side*1.75, side*1.8], [y, y, label_y, label_y], color=colors[i], lw=0.8)
                            ax.text(side*1.85, label_y, f'{labels[i]}\n{100*values[i]/total:.3g}%',
                                    ha='left' if side > 0 else 'right', va='center', fontsize=9)
                    ax.set_xlim(-3.3, 3.3)
                    ax.set_ylim(-half_height, half_height)
                    ax.set_aspect('equal')
                if panel is panels[0]:
                    fig.savefig(folder / name)
                svg = io.StringIO()
                fig.savefig(svg, format='svg')
                panel['svg'].append(svg.getvalue()[svg.getvalue().index('<svg'):])
                fig.clear()
    data = dict(rank=meta['rank'], panels=[dict(p, totals=[str(v) for v in p['totals']]) for p in panels],
                paths=[' / '.join(p) for p in paths], factor=factor, unit=unit)
    template = Path(__file__).with_name('breakdown.html').read_text()
    return template.replace('<!--DATA-->', json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')).replace(
        '<!--WARNINGS-->', html.escape('；'.join(warnings) or '无记录丢弃'))
