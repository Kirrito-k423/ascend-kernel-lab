"""按同核相邻打点聚合；两张总览图复用于 HTML，避免每核额外导出文件。"""
import colorsys
import html
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
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.patches import Patch
    from matplotlib.font_manager import fontManager

    paths, panels = aggregate(meta, events)
    colors = [colorsys.hls_to_rgb(i * 0.381966 % 1, 0.55, 0.65) for i in range(len(paths))]
    columns = min(4, len(panels))
    rows = math.ceil(len(panels) / columns)
    cell_w, cell_h = 3.6, max(2.6, len(paths) * 0.17 + 0.8)
    labels = ['\n'.join(textwrap.wrap(f'{i+1}: ' + ' / '.join(p), 48)) for i, p in enumerate(paths)]
    legend_rows = math.ceil(len(paths) / columns)
    legend_h = 0.3 + legend_rows * (0.17 * max(s.count('\n') + 1 for s in labels) + 0.12)
    width, height = columns * cell_w, 0.7 + rows * cell_h + legend_h
    factor, unit = (1 / clock_mhz, 'us') if clock_mhz else (1, 'cycle')
    max_duration = max(max(p["totals"]) / p["divisor"] for p in panels) * factor
    fonts = [n for n in ("Noto Sans CJK SC", "PingFang SC", "DejaVu Sans") if n in {f.name for f in fontManager.ttflist}]
    for kind, name in enumerate(REPORTS):
        fig = Figure(figsize=(width, height), dpi=100)
        FigureCanvasAgg(fig)
        fig.text(0.02, 1 - 0.18 / height, f'Rank {meta["rank"]} | type duration / observed core E2E', fontsize=12, va='top')
        fig.text(0.02, 1 - 0.42 / height, 'First-to-last retained marker; not full kernel latency. '
                 + ('WARNING: truncated prefixes.' if warnings else 'No dropped records.'), fontsize=9, va='top')
        for index, panel in enumerate(panels):
            x, y = index % columns * cell_w, 0.7 + index // columns * cell_h
            panel['box'] = [x * 100, y * 100, cell_w * 100, cell_h * 100]
            ax = fig.add_axes([(x + 0.48) / width, (height - y - cell_h + 0.48) / height,
                               (cell_w - 0.65) / width, (cell_h - 0.95) / height])
            values = [v * factor / panel['divisor'] for v in panel['totals']]
            total = sum(values)
            ax.set_title(panel['label'] + (f' | E2E {total:.3g} {unit}' if panel['valid'] else ' | E2E N/A'), fontsize=9)
            if not panel['valid'] or not total:
                ax.text(0.5, 0.5, 'No measured interval' if not panel['valid'] else 'Zero span; share N/A',
                        transform=ax.transAxes, ha='center', fontsize=9)
                ax.set_axis_off()
            elif kind == 0:
                ax.barh(range(len(paths)), values, color=colors)
                ax.set_yticks(range(len(paths)), [str(i+1) for i in range(len(paths))], fontsize=8)
                ax.invert_yaxis()
                ax.set_xlabel(f'Total duration ({unit})', fontsize=8)
                ax.set_xlim(0, max_duration * 1.08)
                ax.tick_params(axis='x', labelsize=8)
            else:
                active = [i for i, v in enumerate(values) if v > 0]
                ax.pie([values[i] for i in active], colors=[colors[i] for i in active],
                       labels=[f'{i+1}: {values[i]/total:.1%}' if values[i]/total >= 0.05 else '' for i in active],
                       textprops=dict(fontsize=8), startangle=90)
        fig.legend([Patch(color=c) for c in colors], labels, loc='lower center', ncol=columns,
                   prop=dict(size=8, family=fonts), frameon=False, bbox_to_anchor=(0.5, 0.05 / height))
        fig.savefig(folder / name)
        fig.clear()
    data = dict(panels=[dict(p, totals=[str(v) for v in p['totals']]) for p in panels],
                paths=[' / '.join(p) for p in paths], colors=['#%02x%02x%02x' % tuple(round(c*255) for c in rgb) for rgb in colors],
                width=fig.canvas.get_width_height()[0], height=fig.canvas.get_width_height()[1], factor=factor, unit=unit)
    template = Path(__file__).with_name('breakdown.html').read_text()
    return template.replace('<!--DATA-->', json.dumps(data, ensure_ascii=False).replace('<', '\\u003c')).replace(
        '<!--WARNINGS-->', html.escape('；'.join(warnings) or '无记录丢弃'))
