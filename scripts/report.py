#!/usr/bin/env python3
"""离线校验原始采样并生成CSV、PNG/SVG、HTML和Trace JSON。"""
from pathlib import Path
import argparse, csv, html, json, sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"python"))
from akl.cases import Case
from akl.trace import decode, duration_ticks
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

def q(values,p): return float(np.percentile(values,p))

def analyse(root):
    manifest=json.loads((root/"manifest.json").read_text())
    if manifest["status"]!="validated": raise ValueError("运行未完成验证，不能生成有效性能报告")
    if manifest["schema_version"]!="akl.micro.v1": raise ValueError("未知manifest版本")
    freq=manifest["clock"]["frequency_hz"]
    if not isinstance(freq,(float,int)) or freq<=0: raise ValueError("缺少有效频率")
    tick_us=1e6/freq
    summaries=[]
    for record in manifest["cases"]:
        if record["status"]!="validated": raise ValueError("存在未通过配置")
        case=Case(**record["case"]); folder=root/case.name
        samples=json.loads((folder/"samples.json").read_text())
        if len(samples)!=record["valid_launches"]: raise ValueError("样本数不匹配")
        trace_samples=[]
        for s in samples:
            if s["correctness"] is not True: raise ValueError("存在错误样本")
            if s["trace"]:
                raw=np.load(folder/f"trace_{s['launch_id']}.npy",allow_pickle=False)
                events=decode(raw,manifest["run_id"],s["launch_id"],manifest["device"],record["expected_count"])
                ticks=[duration_ticks(events,c) for c in range(case.cores)]
                if ticks!=s["per_core_ticks"]: raise ValueError("样本计时与原始记录不一致")
                if not s["warmup"]: trace_samples.append((s,events))
        if not trace_samples: raise ValueError("没有有效计时样本")
        with (folder/'samples.csv').open('w') as f:
            fields=['launch_id','warmup','trace','block_id','correctness','host_launch_sync_us','ticks','loops']
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
            for sample in samples:
                for core in range(case.cores):
                    w.writerow(dict(launch_id=sample['launch_id'],warmup=sample['warmup'],trace=sample['trace'],
                        block_id=core,correctness=sample['correctness'],host_launch_sync_us=sample['host_launch_sync_us'],
                        ticks=sample['per_core_ticks'][core] if sample['trace'] else '',loops=case.loops))
        times=[max(s["per_core_ticks"])*tick_us/case.loops for s,e in trace_samples]
        starts=[]
        for s,ev in trace_samples:
            entry=[int(e["tick"]) for e in ev if e["event_id"]==0]
            starts.append((max(entry)-min(entry))*tick_us)
        on=[s["host_launch_sync_us"] for s in samples if s["trace"] and not s["warmup"]]
        off=[s["host_launch_sync_us"] for s in samples if not s["trace"] and not s["warmup"]]
        if len(on)!=len(off): raise ValueError("开关对照数量不同")
        summary=dict(case=case.name,op=case.op,cores=case.cores,loops=case.loops,
                     samples=len(times),max_core_per_call_p50_us=q(times,50),
                     max_core_per_call_p95_us=q(times,95),std_us=float(np.std(times)),
                     entry_spread_raw_unverified_p50_us=q(starts,50),
                     host_trace_p50_us=q(on,50),host_plain_p50_us=q(off,50),
                     paired_host_delta_p50_us=q(np.array(on)-np.array(off),50))
        if case.op in (1,4):
            summary["payload_bytes_per_core"]=case.payload_bytes*case.block_count
            summary["payload_over_slowest_core_GBps"]=summary["payload_bytes_per_core"]/summary["max_core_per_call_p50_us"]/1000
        else:
            summary["payload_bytes_per_core"]=None
            summary["payload_over_slowest_core_GBps"]=None
        summaries.append(summary)
        selected=trace_samples[len(trace_samples)//2][1]
        render_timeline(folder,case,selected,tick_us)
        write_trace(folder,selected,tick_us)
    fields=list(summaries[0])
    with (root/"summary.csv").open("w") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(summaries)
    (root/"summary.json").write_text(json.dumps(summaries,ensure_ascii=False,indent=2))
    render_overview(root,manifest,summaries)
    write_report(root,manifest,summaries)
    return summaries

def render_timeline(folder,case,events,tick_us):
    rows={}
    for e in events: rows.setdefault(e["block_id"],{})[e["event_id"]]=int(e["tick"])
    origin=min(r[0] for r in rows.values())
    fig,ax=plt.subplots(figsize=(13,max(3.5,case.cores*.17+1.8)))
    for c,row in rows.items():
        for a,b,color,label in ((0,1,"#dce4ef","entry to ready"),(2,3,"#1e78b5","measured loop"),(3,4,"#f2a541","output completion")):
            ax.barh(c,(row[b]-row[a])*tick_us,left=(row[a]-origin)*tick_us,color=color,
                    height=.7,label=label if c==0 else None)
        ax.plot((row[0]-origin)*tick_us,c,"|",color="#bd3154",markersize=8)
    ax.set_yticks(range(case.cores)); ax.set_yticklabels([f"AIV {c}" for c in range(case.cores)],fontsize=7)
    ax.invert_yaxis(); ax.grid(axis="x",alpha=.2)
    ax.set_xlabel("Microseconds from common raw SYS_CNT origin (cross-core alignment UNVERIFIED)")
    ax.set_title(f"{case.name}\n{case.loops} serialized calls; raw timestamps retained",loc="left")
    ax.legend(loc="upper right",fontsize=8)
    fig.tight_layout()
    for ext in ("png","svg"): fig.savefig(folder/f"timeline.{ext}",dpi=160)
    plt.close(fig)

def write_trace(folder,events,tick_us):
    origins={e["block_id"]:int(e["tick"]) for e in events if e["event_id"]==0}
    for mode in ("per_core","raw_unverified"):
        common=min(origins.values()); out=[]
        for e in events:
            origin=origins[e["block_id"]] if mode=="per_core" else common
            out.append(dict(name=e["name"],cat=e["boundary"],ph="i",s="t",pid=e["device_id"],tid=e["block_id"],
                ts=(int(e["tick"])-origin)*tick_us,
                args={"raw_tick":e["tick"],"launch_id":e["launch_id"],"alignment":mode}))
        for core in origins:
            row={e["event_id"]:e for e in events if e["block_id"]==core}
            origin=origins[core] if mode=="per_core" else common
            out.append(dict(name="measured_loop",cat="serialized_completion",ph="X",pid=row[2]["device_id"],tid=core,
                ts=(int(row[2]["tick"])-origin)*tick_us,
                dur=(int(row[3]["tick"])-int(row[2]["tick"]))*tick_us,
                args={"alignment":mode}))
        (folder/f"trace_{mode}.json").write_text(json.dumps({"traceEvents":out,"displayTimeUnit":"ms",
            "metadata":{"clock_alignment":mode,"warning":"不得将核内归零或未经校准的SYS_CNT当成跨核绝对时间"}},ensure_ascii=False))

def render_overview(root,manifest,summaries):
    records={r["case"]["name"]:r["case"] for r in manifest["cases"]}
    copy=[s for s in summaries if s["op"] in (1,4) and not records[s["case"]]["delay_ticks"]]
    if copy:
        fig,axes=plt.subplots(1,2,figsize=(13,4.5),squeeze=False)
        for ax,op,title in zip(axes[0],(1,4),("GM to UB","UB to GM")):
            for cores in sorted({s["cores"] for s in copy}):
                vals=[s for s in copy if s["op"]==op and s["cores"]==cores]
                vals.sort(key=lambda s:(records[s["case"]]["payload_bytes"],records[s["case"]]["gap_bytes"],records[s["case"]]["block_count"],records[s["case"]]["loops"]))
                labels=[f'{records[s["case"]]["payload_bytes"]}/{records[s["case"]]["gap_bytes"]}/{records[s["case"]]["block_count"]}/L{s["loops"]}' for s in vals]
                ax.plot(labels,[s["max_core_per_call_p50_us"] for s in vals],marker="o",label=f"{cores} AIV")
            ax.set_title(title); ax.set_xlabel("Payload / gap (bytes) / blocks / loops")
            ax.tick_params(axis='x',labelrotation=40,labelsize=7)
            ax.set_ylabel("Slowest-AIV per-call p50 (us)"); ax.grid(alpha=.2); ax.legend()
        fig.suptitle("DataCopy + completion synchronization; reused working set")
        fig.tight_layout()
        for ext in ("png","svg"): fig.savefig(root/f"datacopy.{ext}",dpi=160)
        plt.close(fig)
    gather=sorted([s for s in summaries if s["op"]==2],key=lambda s:s["case"])
    if gather:
        fig,ax=plt.subplots(figsize=(12,max(4,len(gather)*.25)))
        ax.barh([s["case"] for s in gather],[s["max_core_per_call_p50_us"] for s in gather],color="#258c83")
        ax.invert_yaxis(); ax.set_xlabel("Slowest-AIV per-call p50 (us), includes completion sync")
        ax.set_title("GatherMask: output order and retained count verified"); ax.grid(axis="x",alpha=.2)
        fig.tight_layout()
        for ext in ("png","svg"): fig.savefig(root/f"gathermask.{ext}",dpi=160)
        plt.close(fig)

def write_report(root,manifest,rows):
    lines=["# A3 DataCopy / GatherMask 实测", "",
           f"- SoC：{manifest['hardware']['soc']}；可用AIV：{manifest['hardware']['aiv_count']}；配置数：{len(rows)}。",
           f"- 每配置通过预热、采集开启和关闭对照，全部启动均校验输出；时间频率：{manifest['clock']['frequency_hz']} Hz。",
           "- 指标为每次启动中最慢AIV的循环总时间/循环次数，再跨启动取分位数。包含循环和完成同步，不是裸指令延迟。",
           "- 原始SYS_CNT时间线保留首点偏移，但跨核时钟尚未校准；未做跨卡校准。",
           "- Host差值含Python调用、launch、同步及调度噪声，不能直接当作设备插桩开销；不扣除负差值。",
           "", "| 配置 | AIV | p50 μs/次 | p95 μs/次 |", "|---|---:|---:|---:|"]
    lines += [f"| {s['case']} | {s['cores']} | {s['max_core_per_call_p50_us']:.4f} | {s['max_core_per_call_p95_us']:.4f} |" for s in sorted(rows,key=lambda s:s["case"])]
    (root/"report.md").write_text("\n".join(lines)+"\n")
    options="".join(f'<option value="{html.escape(s["case"],quote=True)}">{html.escape(s["case"])}</option>' for s in sorted(rows,key=lambda s:s["case"]))
    embedded_rows=json.dumps(rows).replace('<', '\\u003c')
    body=f"""<!doctype html><html lang="zh"><meta charset="utf-8"><title>Ascend 接口实验</title>
<style>body{{font:16px system-ui;max-width:1250px;margin:40px auto;padding:0 24px;color:#19334b;background:#f5f8fb}}section{{background:white;padding:24px;border-radius:12px;margin:20px 0}}img{{width:100%}}select{{padding:12px;width:100%}}a{{color:#146fa0}}pre{{white-space:pre-wrap}}</style>
<h1>DataCopy 多 AIV 与 GatherMask 实测</h1>
<p>{len(rows)} 个配置通过 · {html.escape(manifest['hardware']['soc'])} · 保留原始数据</p>
<section><b>如何读图</b><p>计时包含API、循环和完成同步。时间线采用原始 SYS_CNT 共同起点，<b>跨核时钟对齐未验证</b>，首点代表到达插桩位置。</p>
<a href="summary.csv">下载统计CSV</a> · <a href="manifest.json">环境与参数</a> · <a href="report.md">完整说明</a></section>
<section><h2>选择配置</h2><select id="case">{options}</select><p id="links"></p><img id="timeline"><pre id="stats"></pre></section>
<section><h2>DataCopy</h2><img src="datacopy.svg"></section>
<section><h2>GatherMask</h2><img src="gathermask.svg"></section>
<script>const rows={embedded_rows};
const selector=document.getElementById('case');function update(){{const name=selector.value;
document.getElementById('timeline').src=name+'/timeline.svg';
const l=document.getElementById('links');l.replaceChildren();
for(const [title,file] of [['PNG','timeline.png'],['原始事件','events.jsonl'],['逐次样本','samples.json'],['核内Trace','trace_per_core.json']]){{const a=document.createElement('a');a.textContent=title+'  ';a.href=name+'/'+file;l.append(a);}}
document.getElementById('stats').textContent=JSON.stringify(rows.find(r=>r.case===name),null,2);}}
selector.onchange=update;update();</script></html>"""
    (root/"report.html").write_text(body)

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("run",type=Path,help="完整实验结果目录")
    a=p.parse_args(); result=analyse(a.run.resolve());print(f"已校验并生成 {len(result)} 个配置的离线报告")
