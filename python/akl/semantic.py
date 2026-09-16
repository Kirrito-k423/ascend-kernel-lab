"""语义字面量映射、原始 ABI 解码和分层 SVG/HTML；不推断 begin/end 或完成同步。"""
import html
import json
import math
import re
import struct
from collections import Counter
from itertools import groupby
from pathlib import Path

MAGIC = 0x414B4C5452433031


def path_hash(path):
    value = 2166136261
    for part in path:
        for byte in part.encode("utf-8") + b"\0":
            value = ((value ^ byte) * 16777619) & 0xffffffff
    return value


def event_map(sources):
    # 先分词，避免把注释、字符串中的 DebugClock 当作调用；不展开宏和变量。
    token = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|[A-Za-z_]\w*|[^\s]', re.S)
    mapping = {}
    for source in sources:
        tokens = [m.group() for m in token.finditer(Path(source).read_text())
                  if not m.group().startswith(("//", "/*"))]
        for i, name in enumerate(tokens):
            if name not in ("DebugClock", "AKL_DEBUG_CLOCK") or tokens[i-1:i] == ["define"]:
                continue
            if tokens[i+1:i+2] != ["("]:
                continue
            j = i + 2
            if name == "AKL_DEBUG_CLOCK":
                j += 2  # 首参是记录器标识符；定义中的转发宏在上面跳过。
            path = []
            while j < len(tokens) and tokens[j].startswith('"'):
                part = json.loads(tokens[j])  # 接受 JSON 兼容的 C++ UTF-8 字面量转义。
                if not part or "\0" in part:
                    raise ValueError("语义层级不能为空或含 NUL")
                path.append(part)
                j += 1
                if tokens[j:j+1] != [","]:
                    break
                j += 1
                if tokens[j:j+1] == [")"]:
                    raise ValueError("打点参数不能以逗号结束")
            if not path or tokens[j:j+1] != [")"]:
                # 转发宏的 __VA_ARGS__ 不是实际打点。
                if tokens[j:j+1] == ["__VA_ARGS__"]:
                    continue
                raise ValueError(f"{source}: {name} 仅支持字符串字面量参数")
            key = path_hash(path)
            if key in mapping and mapping[key] != path:
                raise ValueError(f"事件哈希冲突：{mapping[key]} / {path}")
            mapping[key] = path
    if not mapping:
        raise ValueError("没有找到语义打点")
    return mapping


def decode_capture(folder, mapping):
    meta = json.loads((folder / "capture.json").read_text())
    if meta.get("schema") != "akl.semantic.v1" or meta.get("alignment") != "unverified":
        raise ValueError("未知采集协议或时钟对齐状态")
    capacity, blocks = meta["capacity"], meta["blocks"]
    if any(type(meta.get(k)) is not int or meta[k] < 0 for k in ("rank", "device")):
        raise ValueError("rank/device 必须是非负整数")
    if type(capacity) is not int or capacity <= 0 or capacity % 2 or type(blocks) is not int or blocks <= 0:
        raise ValueError("容量或通道数非法")
    words = 8 + 2 * capacity
    raw = (folder / "trace.bin").read_bytes()
    if len(raw) != blocks * words * 8:
        raise ValueError("记录区长度不匹配")
    events, warnings = [], []
    for block, row in enumerate(struct.iter_unpack(f"<{words}Q", raw)):
        if row[:2] != (MAGIC, 1) or row[7] != 1 or row[4] != block or row[2] > capacity:
            raise ValueError(f"block {block} 未提交或 ABI 损坏")
        if row[3]:
            warnings.append(f"block {block}: dropped={row[3]}，仅展示保留前缀")
        previous, occurrences = -1, Counter()
        for seq in range(row[2]):
            key, tick = row[8+2*seq:10+2*seq]
            if key not in mapping or tick < previous:
                raise ValueError("事件映射不匹配或同核 cycle 回退")
            previous = tick
            occurrences[key] += 1
            events.append(dict(block=block, subblock=row[5], sequence=seq, event_id=key,
                               occurrence=occurrences[key], tick=str(tick), path=mapping[key]))
    if not events:
        raise ValueError("没有已提交事件")
    return meta, events, warnings


def render(folder, meta, events, warnings, clock_mhz=None):
    if clock_mhz is not None and (not math.isfinite(clock_mhz) or clock_mhz <= 0):
        raise ValueError("clock MHz 必须是有限正数")
    origin = min(int(e["tick"]) for e in events)
    extent = max(int(e["tick"]) - origin for e in events) or 1
    depth = max(len(e["path"]) for e in events)
    stride = depth * 16 + 8
    ticks = sorted({extent * i // 5 for i in range(6)})
    positions = [100 + 1040 * tick / extent for tick in ticks]
    ruler = ['<rect width="1180" height="40" fill="#fff"/>', '<text x="8" y="16">Δcycle</text>']
    if clock_mhz is not None:
        ruler.append('<text x="8" y="32">µs</text>')
    for tick, x in zip(ticks, positions):
        anchor = "start" if tick == 0 else "end" if tick == extent else "middle"
        ruler.append(f'<path d="M{x},35 v5" stroke="#64748b"/>'
                     f'<text x="{x}" y="16" text-anchor="{anchor}">{tick:,}</text>')
        if clock_mhz is not None:
            ruler.append(f'<text x="{x}" y="32" text-anchor="{anchor}">{tick / clock_mhz:,.3f}</text>')
    ruler = ''.join(ruler)
    rows, lanes = [], []
    for block in range(meta["blocks"]):
        lane = [e for e in events if e["block"] == block]
        svg = [f'<text x="8" y="12">block {block}</text>']
        for level in range(depth):
            # 只合并连续的父路径；叶子保留每次命中，循环边界不消失。
            def group_key(item):
                index, event = item
                prefix = tuple(event["path"][:level+1])
                return prefix, index if level >= len(event["path"])-1 else None
            for (prefix, _), group in groupby(enumerate(lane), key=group_key):
                segment = list(group)
                if len(prefix) <= level:
                    continue
                first, last = segment[0][0], segment[-1][0]
                start = int(lane[first]["tick"]) - origin
                end = int(lane[min(last+1, len(lane)-1)]["tick"]) - origin
                title = html.escape(f'{" / ".join(prefix)} | seq={first}…{last} | '
                                    f'cycle={origin+start} → {origin+end} | Δcycle={end-start}'
                                    + (f' | Δµs={(end-start)/clock_mhz:.3f}' if clock_mhz else ''))
                x, width = 100 + 1040 * start / extent, 1040 * (end-start) / extent
                hue = path_hash(prefix[:1]) % 360
                light = min(86, 40 + level * 10 + path_hash(prefix) % 8)
                saturation = 40 + path_hash(prefix) % 35
                color = f'hsl({hue} {saturation}% {light}%)'
                label = html.escape(prefix[-1][:max(0, int(width/9)-1)])
                svg.append(f'<g data-level="{level}"><title>{title}</title>'
                           f'<rect x="{x}" y="{level*16}" width="{max(width, 1)}" height="14" '
                           f'fill="{color}" stroke="white" stroke-width="0.5"/>'
                           f'<text x="{x+3}" y="{level*16+11}" font-size="11">{label}</text></g>')
        lanes.append(''.join(svg))
        for event in lane:
            rows.append(f'<tr><td>{block}/{event["subblock"]}</td><td>{event["sequence"]}</td><td>{event["occurrence"]}</td>'
                        f'<td>{event["tick"]}</td><td>{html.escape(" / ".join(event["path"]))}</td></tr>')
    height = 8 + meta["blocks"] * stride
    grid = ''.join(f'<line x1="{x}" x2="{x}" y1="0" y2="100%" stroke="#cbd5e1" stroke-dasharray="2 3"/>'
                   for x in positions)
    bands = ''.join(f'<g class="lane" data-block="{b}" transform="translate(0,{8+b*stride})">{lane}</g>'
                    for b, lane in enumerate(lanes))
    warning = html.escape("；".join(warnings) or "无记录丢弃")
    svg_open = '<svg xmlns="http://www.w3.org/2000/svg" style="font:12px system-ui;fill:#183047" '
    # SVG 是静态导出：每 8 个 block 重复刻度；HTML 单独使用可悬浮的共同刻度。
    clock_label = f"; clock MHz={clock_mhz:g}" if clock_mhz else ""
    static = [grid, '<rect width="1180" height="24" fill="white"/>',
              f'<text x="8" y="17">origin cycle={origin}{clock_label}; alignment UNVERIFIED</text>']
    for block, lane in enumerate(lanes):
        y = 24 + block * stride + (block // 8 + 1) * 40
        if block % 8 == 0:
            static.append(f'<g class="ruler" transform="translate(0,{y-40})">{ruler}</g>')
        static.append(f'<g class="lane" transform="translate(0,{y+8})">{lane}</g>')
    static_height = 24 + height + ((meta["blocks"] + 7) // 8) * 40
    drawing = svg_open + f'width="1180" height="{static_height}" viewBox="0 0 1180 {static_height}">' + ''.join(static) + '</svg>'
    counts = Counter((e["block"], e["subblock"], e["event_id"]) for e in events)
    names = {e["event_id"]: e["path"] for e in events}
    summary = [dict(block=b, subblock=s, event_id=key, path=names[key], count=count)
               for (b, s, key), count in counts.items()]
    totals = ''.join(f'<tr><td>{s["block"]}/{s["subblock"]}</td><td>{s["count"]}</td>'
                     f'<td>{html.escape(" / ".join(s["path"]))}</td></tr>' for s in summary)
    page = f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>语义 cycle 时间线</title>
<style>body{{font:14px system-ui;margin:20px;color:#183047}}svg{{display:block;width:100%}}.chart{{overflow:auto;max-height:72vh;border:1px solid #cbd5e1}}.canvas{{min-width:900px}}.ruler{{position:sticky;top:0;z-index:1;background:white;border-bottom:1px solid #cbd5e1}}output{{display:block;padding:4px 8px;font:12px ui-monospace,monospace;min-height:18px}}td,th{{padding:4px 8px;text-align:left;border-bottom:1px solid #ddd}}input{{width:60px}}.controls{{margin:12px 0}}details{{margin-top:18px}}</style>
<h1>语义 cycle 时间线 · rank {meta['rank']} / device {meta['device']}</h1>
<p>共同原始起点 {origin}；范围 Δcycle=0…{extent}；跨核对齐未验证。{warning}</p>
<p>每段表示该打点至下一个打点，末点仅作标记。移动鼠标对齐各 block，单击固定对齐线，再次单击解除。</p>
<div class="controls"><label>显示前 <input id="depth" type="number" min="1" max="{depth}" value="{depth}"> 级</label>
 · {f'按用户指定的 {clock_mhz:g} MHz 换算 µs' if clock_mhz else '刻度单位 cycle；提供 --clock-mhz 可增加 µs 刻度'}</div>
<div class="chart" tabindex="0" role="region" aria-label="block 时间线"><div class="canvas"><div class="ruler">{svg_open}viewBox="0 0 1180 40">{ruler}</svg>
<output id="readout" aria-live="off">移动鼠标读取 cycle</output></div>
{svg_open}id="timeline" viewBox="0 0 1180 {height}">{grid}{bands}
<line id="cursor" x1="100" x2="100" y1="0" y2="100%" stroke="#0f172a" stroke-width="1" pointer-events="none" visibility="hidden"/></svg>
</div></div><details><summary>打点次数（仅统计保留记录）</summary>
<table><tr><th>block/subblock</th><th>次数</th><th>语义路径</th></tr>{totals}</table></details>
<details><summary>原始绝对 cycle（整数）</summary>
<table><tr><th>block/subblock</th><th>序号</th><th>该点第几次</th><th>cycle</th><th>语义路径</th></tr>{''.join(rows)}</table></details>
<script>
const timeline=document.getElementById('timeline'), cursor=document.getElementById('cursor');
const origin=BigInt('{origin}'), extent=BigInt('{extent}'), mhz={json.dumps(clock_mhz)};
let pinned=false;
function locate(e) {{
    const p=new DOMPoint(e.clientX,e.clientY).matrixTransform(timeline.getScreenCTM().inverse());
    const fraction=Math.max(0,Math.min(1,(p.x-100)/1040));
    const x=100+1040*fraction;
    cursor.setAttribute('x1',x); cursor.setAttribute('x2',x); cursor.setAttribute('visibility','visible');
    // 绝对 cycle 全程用 BigInt；鼠标位置是插值估计，不能先把原始 tick 转成 Number。
    const delta=extent*BigInt(Math.round(fraction*1000000))/1000000n;
    document.getElementById('readout').textContent=(pinned?'已固定':'鼠标估计')+
        ' · Δcycle≈'+delta+' · cycle≈'+(origin+delta)+(mhz?' · Δµs≈'+(Number(delta)/mhz).toFixed(3):'');
}}
timeline.addEventListener('pointermove',e=>{{if(!pinned) locate(e);}});
timeline.addEventListener('click',e=>{{pinned=!pinned; locate(e);}});
document.getElementById('depth').oninput=e=>{{
    const n=Math.max(1,Math.min({depth},Math.trunc(Number(e.target.value)||1)));
    timeline.querySelectorAll('[data-level]').forEach(r=>r.style.display=Number(r.dataset.level)<n?'':'none');
    timeline.querySelectorAll('.lane').forEach((r,b)=>r.setAttribute('transform',`translate(0,${{8+b*(n*16+8)}})`));
    timeline.setAttribute('viewBox',`0 0 1180 ${{8+{meta['blocks']}*(n*16+8)}}`);
}};
</script></html>'''
    (folder / "semantic.html").write_text(page)
    (folder / "semantic.svg").write_text(drawing)
    with (folder / "semantic.jsonl").open("w") as out:
        for event in events:
            out.write(json.dumps(dict(meta, launch_id=folder.name, **event), ensure_ascii=False) + "\n")
    (folder / "counts.json").write_text(json.dumps(dict(counts=summary, warnings=warnings), indent=2))



def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--source", type=Path, nargs="+", required=True, help="编译所用的语义打点源码")
    parser.add_argument("--clock-mhz", type=float, help="用户确认的 cycle 时钟频率（MHz），用于 µs 刻度")
    args = parser.parse_args()
    meta, events, warnings = decode_capture(args.capture, event_map(args.source))
    render(args.capture, meta, events, warnings, args.clock_mhz)
    print(f"已导出 {len(events)} 个原始事件；{len(warnings)} 个丢弃告警；{args.capture / 'semantic.html'}")


if __name__ == "__main__":
    main()
