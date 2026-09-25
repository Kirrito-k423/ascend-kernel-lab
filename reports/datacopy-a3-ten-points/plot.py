"""从同目录CSV和原始tick重算并绘制10点曲线，不拟合、不混合测量条件。"""
import csv
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

root=Path(__file__).resolve().parent
info=json.loads((root/'coverage.json').read_text())
rows=list(csv.DictReader((root/'points.csv').open()))
sizes=info['selected_bytes']
assert len(rows)==20 and len(sizes)==10 and len(set(sizes))==10
for r in rows:
    values=np.asarray(json.loads(r['raw_total_ticks']))*1e6/info['clock_hz']/info['conditions']['loops']
    assert len(values)==int(r['samples'])==20
    for key,p in [('p50_us',50),('p95_us',95)]:
        assert abs(float(r[key])-np.percentile(values,p))<1e-12
    assert abs(float(r['payload_GBps'])-int(r['payload_bytes'])/float(r['p50_us'])/1000)<1e-10
available={f.name for f in font_manager.fontManager.ttflist}
font=next((f for f in ['PingFang SC','Noto Sans CJK SC','Microsoft YaHei','Arial Unicode MS'] if f in available),'DejaVu Sans')
plt.rcParams.update({'font.family':font,'axes.unicode_minus':False,'font.size':12,'axes.spines.top':False,'axes.spines.right':False})
labels=['32 B','64 B','128 B','256 B','512 B','1 KiB','4 KiB','8 KiB','14 KiB','32 KiB']
for metric,title,ylabel,filename in [
    ('p50_us','A3 DataCopy：每条曲线 10 个实测点','每次完成耗时（μs，p50）','latency-10.png'),
    ('payload_GBps','A3 DataCopy：逐次等待扫描，尚未测到吞吐平台','有效 payload 吞吐（GB/s）','throughput-10.png')]:
    fig,ax=plt.subplots(figsize=(11.8,5.8))
    fig.patch.set_facecolor('#f7f9fc');ax.set_facecolor('white')
    fig.suptitle(title,x=.085,y=.97,ha='left',fontsize=21,fontweight='bold',color='#15334d')
    fig.text(.085,.90,'Ascend910_9382 · CANN 9.1.0-beta.1 · 单 AIV · uint32 · DataCopy(params)',color='#486172',fontsize=11)
    for direction,label,color in [('GM_UB','GM → UB','#147d92'),('UB_GM','UB → GM','#d66a3a')]:
        series=sorted((r for r in rows if r['direction']==direction),key=lambda r:int(r['payload_bytes']))
        assert [int(r['payload_bytes']) for r in series]==sizes
        y=[float(r[metric]) for r in series]
        ax.plot(sizes,y,'o-',label=label,color=color,lw=2.3,markersize=6)
        if metric=='p50_us':ax.fill_between(sizes,y,[float(r['p95_us']) for r in series],color=color,alpha=.13)
        ax.annotate(f'{y[-1]:.3f}' if metric=='p50_us' else f'{y[-1]:.2f}',
                    (sizes[-1],y[-1]),xytext=(9,6 if (direction=='GM_UB')==(metric=='payload_GBps') else -14),textcoords='offset points',color=color,fontsize=11)
    ax.set_xscale('log',base=2);ax.set_xticks(sizes,labels,rotation=25,ha='right')
    ax.set_xlim(27,55000);ax.set_ylim(bottom=0);ax.set_ylabel(ylabel)
    ax.set_xlabel('每次搬运的数据量（横轴为对数刻度）',labelpad=8)
    ax.grid(axis='both',alpha=.16);ax.legend(loc='upper left',frameon=False)
    foot='每点 20 次计时样本；线为 p50，阴影为 p50–p95。' if metric=='p50_us' else '每点吞吐 = 有效字节 ÷ p50 完成耗时；单向计量。'
    fig.text(.085,.065,foot+'连线仅引导阅读，无拟合或外推。',color='#486172',fontsize=10)
    fig.text(.085,.025,'batch=1 · 每次计时128次调用 · 重复小工作集。32 KiB 处仍在上升；需扩大数据量与批量后再判断平台。',color='#486172',fontsize=10)
    fig.subplots_adjust(left=.085,right=.96,bottom=.23,top=.83)
    fig.savefig(root/filename,dpi=160,facecolor=fig.get_facecolor());plt.close(fig)
print('Recomputed p50/p95 and throughput for all 20 points; wrote two PNGs.')
