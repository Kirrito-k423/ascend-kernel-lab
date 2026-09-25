"""从CSV原始tick重算全部点，再画工作集与固定UB批量对照。"""
import csv
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

root = Path(__file__).resolve().parent
info = json.loads((root / 'coverage.json').read_text())
rows = list(csv.DictReader((root / 'points.csv').open()))
for r in rows:
    for k in ('repeat', 'windows', 'bytes', 'batch', 'loops', 'working_set_bytes', 'samples'): r[k] = int(r[k])
    values = np.asarray(json.loads(r['raw_total_ticks'])) * 1e6 / info['clock_hz'] / (r['loops'] * r['batch'])
    assert len(values) == r['samples'] == 20
    for k, p in (('p50_us', 50), ('p95_us', 95)):
        r[k] = float(r[k]); assert abs(r[k] - np.percentile(values, p)) < 1e-10
    r['GBps'] = float(r['GBps']); assert abs(r['GBps'] - r['bytes'] / r['p50_us'] / 1000) < 1e-8
fonts = {f.name for f in font_manager.fontManager.ttflist}
plt.rcParams.update({'font.family': next((f for f in ('PingFang SC', 'Noto Sans CJK SC', 'Arial Unicode MS') if f in fonts), 'DejaVu Sans'),
                     'font.size': 12, 'axes.spines.top': False, 'axes.spines.right': False})

def series(ax, selected, key, label, color):
    xs = sorted({r[key] for r in selected})
    bands = [[r['GBps'] for r in selected if r[key] == x] for x in xs]
    assert all(len(b) == 2 for b in bands)
    ax.plot(xs, [np.median(b) for b in bands], 'o-', color=color, label=label, lw=2, ms=5)
    ax.fill_between(xs, [min(b) for b in bands], [max(b) for b in bands], color=color, alpha=.18)

for filename, title, batch_plot in [
    ('plateau.png', 'A3 单 AIV：工作集不同，可达吞吐不同', False),
    ('batch.png', 'A3 单 AIV：固定 128 KiB UB，每批拆成多少次 DataCopy？', True)]:
    fig, axes = plt.subplots(1, 2, figsize=(13.6, 5.7))
    fig.patch.set_facecolor('#f7f9fc')
    fig.suptitle(title, x=.07, ha='left', fontsize=20, fontweight='bold', color='#15334d')
    fig.text(.07, .885, 'Ascend910_9382 · CANN 9.1.0-beta.1 · uint32 / DataCopy(params) · 双 UB 窗口', color='#486172')
    for ax, direction in zip(axes, ('GM_UB', 'UB_GM')):
        for mode, label, color in [('small', '重复小工作集', '#147d92'), ('ring64', '64 MiB 环形工作集', '#d66a3a')]:
            selected = [r for r in rows if r['windows'] == 2 and r['direction'] == direction and r['mode'] == mode
                        and (r['bytes'] * r['batch'] == 65536 if batch_plot else r['batch'] == 1)]
            series(ax, selected, 'batch' if batch_plot else 'bytes', label, color)
        ax.set_title(direction.replace('_', ' → '))
        ax.set_xscale('log', base=2)
        if batch_plot:
            ax.set_xticks([1,2,4,8,16,32,64], ['1','2','4','8','16','32','64'])
            ax.set_xlabel('每批调用数（每批 payload 固定 64 KiB）')
        else:
            ax.set_xticks([32,128,512,2048,8192,32768,98144], ['32 B','128 B','512 B','2 KiB','8 KiB','32 KiB','95.84 KiB'])
            ax.tick_params(axis='x', labelrotation=25)
            ax.set_xlabel('单次 DataCopy 的有效数据量（对数刻度）')
        ax.set_ylim(0, 255); ax.set_ylabel('有效 payload GB/s'); ax.grid(alpha=.17); ax.legend(fontsize=10)
    footer = ('总 UB 固定 128 KiB，两个窗口各 64 KiB；循环均为 8192。增大 batch 同时减小单次 payload。' if batch_plot else
              '每点两轮独立运行，每轮20个计时样本；线为两轮p50带宽中位数，阴影为两轮范围。无拟合或外推。')
    fig.text(.07, .08, footer, color='#486172', fontsize=10)
    fig.text(.07, .035, '包含发射、地址计算、窗口复用等待及最终完成；工作集未证明绕过缓存。单 AIV 结果不等于整卡 HBM 峰值。', color='#486172', fontsize=10)
    fig.subplots_adjust(left=.07, right=.975, top=.80, bottom=.26, wspace=.22)
    fig.savefig(root / filename, dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)
print(f'Recomputed {len(rows)} rows from raw ticks; wrote plateau.png and batch.png.')
