"""语义字面量映射、原始 ABI 解码和分层 SVG/HTML；不推断 begin/end 或完成同步。"""
import colorsys
import html
import json
import math
import re
import struct
from collections import Counter, defaultdict
from itertools import groupby
from pathlib import Path

from .chrome_trace import trace_file, write_capture, interval_work

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
            unit = None
            if path and tokens[j:j+1] != [")"]:
                # 末尾数量是 C++ 表达式，只定位其边界，不在 Python 求值。
                start, depth = j, 0
                while j < len(tokens):
                    if tokens[j] in ('(', '[', '{'): depth += 1
                    if tokens[j] in (')', ']', '}'):
                        if depth == 0: break
                        depth -= 1
                    if depth == 0 and tokens[j] == ',': break
                    j += 1
                if j > start and tokens[j:j+1] == [','] and tokens[j+1:j+2] and tokens[j+1].startswith('"'):
                    unit = json.loads(tokens[j+1])
                    if not unit or "\0" in unit: raise ValueError("处理量单位不能为空或含 NUL")
                    j += 2
                if unit is None: raise ValueError("处理量后必须提供单位字符串字面量")
            if not path or tokens[j:j+1] != [")"]:
                # 转发宏的 __VA_ARGS__ 不是实际打点。
                if tokens[j:j+1] == ["__VA_ARGS__"]:
                    continue
                raise ValueError(f"{source}: {name} 仅支持字符串字面量参数")
            key = path_hash(path + (["@quantity", unit] if unit else []))
            definition = dict(path=path, unit=unit) if unit else path
            if key in mapping and mapping[key] != definition:
                raise ValueError(f"事件哈希冲突：{mapping[key]} / {path}")
            mapping[key] = definition
    if not mapping:
        raise ValueError("没有找到语义打点")
    return mapping


def decode_capture(folder, mapping):
    meta = json.loads((folder / "capture.json").read_text())
    if meta.get("schema") not in ("akl.semantic.v1", "akl.semantic.v2") or meta.get("alignment") != "unverified":
        raise ValueError("未知采集协议或时钟对齐状态")
    capacity, blocks = meta["capacity"], meta["blocks"]
    if any(type(meta.get(k)) is not int or meta[k] < 0 for k in ("rank", "device")):
        raise ValueError("rank/device 必须是非负整数")
    if type(capacity) is not int or capacity <= 0 or capacity % 2 or type(blocks) is not int or blocks <= 0:
        raise ValueError("容量或通道数非法")
    version = 2 if meta["schema"].endswith("v2") else 1
    stride = 4 if version == 2 else 2
    words = 8 + stride * capacity
    raw = (folder / "trace.bin").read_bytes()
    if len(raw) != blocks * words * 8:
        raise ValueError("记录区长度不匹配")
    events, warnings = [], []
    for block, row in enumerate(struct.iter_unpack(f"<{words}Q", raw)):
        if row[:2] != (MAGIC, version) or row[7] != 1 or row[4] != block or row[2] > capacity:
            raise ValueError(f"block {block} 未提交或 ABI 损坏")
        if row[3]:
            warnings.append(f"block {block}: dropped={row[3]}，仅展示保留前缀")
        previous, occurrences, counters = -1, Counter(), {}
        for seq in range(row[2]):
            key, tick = row[8+stride*seq:10+stride*seq]
            if key not in mapping or tick < previous:
                raise ValueError("事件映射不匹配或同核 cycle 回退")
            definition = mapping[key]
            path = definition['path'] if isinstance(definition, dict) else definition
            work = None
            if version == 2 and row[11+stride*seq]:
                value, kind = row[10+stride*seq:12+stride*seq]
                if not isinstance(definition, dict): raise ValueError("处理量或单位非法")
                if kind & 0xffffffff == 4:
                    scope = kind >> 32
                    prior = counters.get((key, scope))
                    if prior and value < prior[0]: raise ValueError("累计计数回退：需要单调累计值或新的 scope")
                    work = dict(amount=str(value-prior[0]) if prior else None, unit=definition['unit'],
                                elapsed_cycle=str(tick-prior[1]) if prior else None, scope=scope,
                                cumulative=str(value), start_tick=str(prior[1]) if prior else None, end_tick=str(tick))
                    counters[(key, scope)] = (value, tick)
                else:
                    amount = value if kind == 1 else struct.unpack('<f', struct.pack('<I', value & 0xffffffff))[0]
                    if kind not in (1, 2) or not math.isfinite(amount) or amount < 0:
                        raise ValueError("处理量或单位非法")
                    work = dict(amount=str(amount), unit=definition['unit'],
                                elapsed_cycle=str(tick-previous) if previous >= 0 else None)
            previous = tick
            occurrences[key] += 1
            events.append(dict(block=block, subblock=row[5], sequence=seq, event_id=key,
                               occurrence=occurrences[key], tick=str(tick), path=path))
            if work is not None: events[-1]["work"] = work
    if not events:
        raise ValueError("没有已提交事件")
    return meta, events, warnings


def segment_color(index, level, duration, scale):
    # 黄金角跳色兼顾相邻差异和多色循环；层级由纵向位置/标签表示。
    hue = ((index * 137.508 + level * 97) % 360) / 360
    # 用实际 sRGB 亮度约束深浅，避免等 HSL lightness 的黄色比蓝色亮很多。
    # 短段更深；最低亮度 0.24，配 #111111 文字仍有 >5:1 的对比度。
    target = 0.24 + 0.48 * math.sqrt(min(1, duration / max(1, scale)))
    low, high = 0.0, 1.0
    for _ in range(16):
        light = (low + high) / 2
        rgb = colorsys.hls_to_rgb(hue, light, 0.78)
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        luminance = sum(c * w for c, w in zip(linear, (0.2126, 0.7152, 0.0722)))
        if luminance < target:
            low = light
        else:
            high = light
    rgb = colorsys.hls_to_rgb(hue, (low + high) / 2, 0.78)
    return '#' + ''.join(f'{round(c * 255):02x}' for c in rgb)


def render(folder, meta, events, warnings, clock_mhz=None, cycle_range=None):
    if clock_mhz is not None and (not math.isfinite(clock_mhz) or clock_mhz <= 0):
        raise ValueError("clock MHz 必须是有限正数")
    for event in events:
        if 'work' in event:
            work = event['work']
            elapsed = int(work['elapsed_cycle']) * (1 / clock_mhz if clock_mhz else .001) if work['elapsed_cycle'] is not None else None
            work.update(elapsed_us=elapsed, rate_per_us=float(work['amount'])/elapsed if elapsed else None)
    origin = min(int(e["tick"]) for e in events)
    extent = max(int(e["tick"]) - origin for e in events) or 1
    left, right = cycle_range if cycle_range is not None else (0, extent)
    if any(type(v) is not int for v in (left, right)) or not 0 <= left < right <= extent:
        raise ValueError(f"cycle 范围须满足 0 <= 起点 < 终点 <= {extent}")
    span = right - left
    depth = max(len(e["path"]) for e in events)
    stride = depth * 16 + 8
    ticks = sorted({left + span * i // 5 for i in range(6)})
    positions = [100 + 1040 * (tick-left) / span for tick in ticks]
    ruler = ['<rect width="1180" height="40" fill="#fff"/>', '<text x="8" y="16">Δcycle</text>']
    if clock_mhz is not None:
        ruler.append('<text x="8" y="32">µs</text>')
    for tick, x in zip(ticks, positions):
        anchor = "start" if tick == left else "end" if tick == right else "middle"
        ruler.append(f'<path d="M{x},35 v5" stroke="#64748b"/>'
                     f'<text x="{x}" y="16" text-anchor="{anchor}">{tick:,}</text>')
        if clock_mhz is not None:
            ruler.append(f'<text x="{x}" y="32" text-anchor="{anchor}">{tick / clock_mhz:,.3f}</text>')
    ruler = ''.join(ruler)
    by_block = defaultdict(list)
    for event in events:
        by_block[event['block']].append(event)
    # 全部 block 共用核内最长跨度；窗口、单位与 block 筛选均不改变配色。
    color_scale = max(int(lane[-1]['tick']) - int(lane[0]['tick']) for lane in by_block.values()) or 1
    rows, lanes = [], []
    for block in range(meta["blocks"]):
        lane = by_block.get(block, [])
        svg = []
        for level in range(depth):
            # 只合并连续的父路径；叶子保留每次命中，循环边界不消失。
            def group_key(item):
                index, event = item
                prefix = tuple(event["path"][:level+1])
                return prefix, index if level >= len(event["path"])-1 else None
            for color_index, ((prefix, _), group) in enumerate(groupby(enumerate(lane), key=group_key)):
                segment = list(group)
                if len(prefix) <= level:
                    continue
                first, last = segment[0][0], segment[-1][0]
                start = int(lane[first]["tick"]) - origin
                end = int(lane[min(last+1, len(lane)-1)]["tick"]) - origin
                title = html.escape(f'{" / ".join(prefix)} | seq={first}…{last} | '
                                    f'cycle={origin+start} → {origin+end} | Δcycle={end-start}'
                                    + (f' | Δµs={(end-start)/clock_mhz:.3f}' if clock_mhz else ''))
                work = interval_work(lane, first) if first == last and level == len(lane[first]['path'])-1 else None
                auxiliary = ''
                if work:
                    auxiliary = ' data-work="' + html.escape(json.dumps(work), quote=True) + '"'
                    title += html.escape(f" | 处理量={work['amount'] if work['amount'] is not None else '基线'} {work['unit']} | 速度={work['rate_per_us'] if work['rate_per_us'] is not None else '不可计算'} {work['unit']}/us")
                    if 'scope' in work:
                        title += html.escape(f" | index={work['scope']} 累计={work['cumulative']} | " +
                            (f"探测区间 cycle={work['start_tick']} → {work['end_tick']}；间隔={work['elapsed_cycle']} cycle" if work['start_tick'] is not None else '首次探测：仅建立基线'))
                visible = (left <= start <= right) if start == end else (end > left and start < right)
                x = 100 + 1040 * max(0, start-left) / span
                width = 1040 * max(0, min(end, right)-max(start, left)) / span
                color = segment_color(color_index, level, end-start, color_scale)
                # 1px 仅作短区间可见性标记；真实时长保留在 title，完整标签供缩放后恢复。
                label = html.escape(prefix[-1][:max(0, int(width/9)-1)])
                svg.append((end-start, f'<g{auxiliary} data-level="{level}" data-start="{start}" data-end="{end}" '
                           f'data-label="{html.escape(prefix[-1], quote=True)}" data-path="{html.escape(" / ".join(prefix), quote=True)}"'
                           + ('' if visible else ' style="display:none"') + f'><title>{title}</title>'
                           f'<rect x="{x}" y="{level*16}" width="{max(width, 1)}" height="14" '
                           f'fill="{color}" stroke="white" stroke-width="0"/>'
                           f'<text x="{x+3}" y="{level*16+11}" font-size="11" fill="#111111">{label}</text></g>'))
        # 短段后画，避免其最小宽度标记被相邻长段遮住。
        lanes.append(f'<text x="8" y="12">block {block}</text>'
                     + ''.join(markup for _, markup in sorted(svg, key=lambda item: -item[0])))
        block_rows = []
        for event in lane:
            work = event.get('work')
            detail = f"{work['amount'] if work['amount'] is not None else '基线'} {work['unit']} / {work['elapsed_cycle'] if work['elapsed_cycle'] is not None else '未知'} cycle；{work['rate_per_us'] if work['rate_per_us'] is not None else '不可计算'} {work['unit']}/us" if work else '—'
            if work and 'scope' in work:
                detail += f"；index={work['scope']} 累计={work['cumulative']}；" + (f"探测 cycle={work['start_tick']} → {work['end_tick']}" if work['start_tick'] is not None else '首次探测：仅建立基线')
            block_rows.append(f'<tr><td>{block}/{event["subblock"]}</td><td>{event["sequence"]}</td><td>{event["occurrence"]}</td>'
                        f'<td>{event["tick"]}</td><td>{html.escape(" / ".join(event["path"]))}</td><td>{html.escape(detail)}</td></tr>')
        rows.append(''.join(block_rows))
    height = 8 + min(meta["blocks"], 8) * stride
    grid = ''.join(f'<line x1="{x}" x2="{x}" y1="0" y2="100%" stroke="#cbd5e1" stroke-dasharray="2 3"/>'
                   for x in positions)
    warning = html.escape("；".join(warnings) or "无记录丢弃")
    svg_open = '<svg xmlns="http://www.w3.org/2000/svg" style="font:12px system-ui;fill:#183047" '
    # SVG 是静态导出：每 8 个 block 重复刻度；HTML 单独使用可悬浮的共同刻度。
    clock_label = f"; clock MHz={clock_mhz:g}" if clock_mhz else ""
    static = [grid, '<rect width="1180" height="24" fill="white"/>',
              f'<text x="8" y="17">origin cycle={origin}{clock_label}; Δcycle window={left}…{right}; alignment UNVERIFIED</text>']
    for block, lane in enumerate(lanes):
        y = 24 + block * stride + (block // 8 + 1) * 40
        if block % 8 == 0:
            static.append(f'<g class="ruler" transform="translate(0,{y-40})">{ruler}</g>')
        static.append(f'<g class="lane" transform="translate(0,{y+8})">{lane}</g>')
    static_height = 32 + meta["blocks"] * stride + ((meta["blocks"] + 7) // 8) * 40
    drawing = svg_open + f'width="1180" height="{static_height}" viewBox="0 0 1180 {static_height}">' + ''.join(static) + '</svg>'
    counts = Counter((e["block"], e["subblock"], e["event_id"]) for e in events)
    names = {e["event_id"]: e["path"] for e in events}
    summary = [dict(block=b, subblock=s, event_id=key, path=names[key], count=count)
               for (b, s, key), count in counts.items()]
    lane_data = []
    for block, lane in enumerate(lanes):
        totals = ''.join(f'<tr><td>{s["block"]}/{s["subblock"]}</td><td>{s["count"]}</td>'
                         f'<td>{html.escape(" / ".join(s["path"]))}</td></tr>' for s in summary if s['block'] == block)
        lane_data.append(dict(svg=lane, rows=rows[block], counts=totals))
    # 用字符串保存未选中的 block；不预先创建数万个 SVG/表格 DOM 节点。
    payload = json.dumps(lane_data, ensure_ascii=False).replace('<', '\\u003c')
    page = f'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>语义 cycle 时间线</title>
<style>body{{font:14px system-ui;margin:20px;color:#183047}}svg{{display:block;width:100%}}.chart{{overflow:auto;max-height:72vh;border:1px solid #cbd5e1}}.canvas{{min-width:900px}}.ruler{{position:sticky;top:0;z-index:1;background:white;border-bottom:1px solid #cbd5e1}}output{{display:block;padding:4px 8px;font:12px ui-monospace,monospace;min-height:18px}}td,th{{padding:4px 8px;text-align:left;border-bottom:1px solid #ddd}}input{{width:60px}}.controls{{margin:12px 0;display:flex;flex-wrap:wrap;gap:6px;align-items:center}}#from,#to{{width:120px}}#timeline{{user-select:none;touch-action:pan-y}}details{{margin-top:18px}}</style>
<h1>语义 cycle 时间线 · rank {meta['rank']} / device {meta['device']}</h1>
<a href="trace.json" download>下载 Chrome Trace JSON（导出时的换算设置，全量 block/区间）</a>
<p>共同原始起点 {origin}；范围 Δcycle=0…{extent}；跨核对齐未验证。{warning}</p>
<p>相邻区间跳色，短段颜色更深；颜色不表示父子关系，层级由纵向位置和标签表示。</p>
<p>每段表示该打点至下一个打点，末点仅作标记。移动鼠标对齐各 block，单击固定对齐线，再次单击解除。</p>
<div class="controls">
<button id="zoom-in">＋ 放大</button><button id="zoom-out">− 缩小</button>
<button id="pan-left">← 平移</button><button id="pan-right">平移 →</button><button id="reset">全范围</button>
<label>起点 Δcycle <input id="from" type="text" inputmode="numeric" value="{left}"></label>
<label>终点 Δcycle <input id="to" type="text" inputmode="numeric" value="{right}"></label><button id="apply">应用范围</button>
<span id="window"></span></div>
<p>Ctrl/⌘＋滚轮以鼠标位置缩放；拖拽框选放大；Shift＋拖拽或滚轮平移。短段最小显示 1px，精确时长见悬停读数。</p>
<div class="controls"><label>显示前 <input id="depth" type="number" min="1" max="{depth}" value="{depth}"> 级</label>
<label>1 cycle = <input id="cycle-us" type="number" step="any" value="{1/clock_mhz if clock_mhz else 0.001}"> µs</label>
<label>坐标轴单位 <select id="unit"><option value="cycle">cycle</option><option value="us">µs</option></select></label>
<span id="conversion">显示换算，可按实际时钟调整</span></div>
<div class="controls"><label>Block <input id="blocks" type="text" style="width:240px" value="0-{min(meta['blocks']-1, 7)}"></label>
<button id="apply-blocks">显示所选 block</button><button id="all-blocks">全部 block</button><span id="block-status"></span></div>
<div class="controls"><label>搜索模块 <input id="search" type="search" style="width:260px" placeholder="完整语义路径中的关键词"></label>
<button id="clear-search">清除搜索</button><span id="search-status" role="status"></span></div>
<p>支持范围与逗号，例如 0-7,16,32-39。默认仅绘制前 8 个 block；统计表也只显示所选 block，完整记录仍在导出的 JSONL 中。</p>
<div class="chart" tabindex="0" role="region" aria-label="block 时间线"><div class="canvas"><div class="ruler">{svg_open}id="axis" viewBox="0 0 1180 40">{ruler}</svg>
<output id="readout" aria-live="off">移动鼠标读取 cycle</output></div>
{svg_open}id="timeline" data-origin="{origin}" data-extent="{extent}" data-mhz="{clock_mhz or ''}" data-left="{left}" data-right="{right}" viewBox="0 0 1180 {height}"><g id="grid">{grid}</g><g id="lanes"></g>
<rect id="selection" y="0" height="100%" fill="#2563eb" opacity="0.15" pointer-events="none" visibility="hidden"/>
<line id="cursor" x1="100" x2="100" y1="0" y2="100%" stroke="#0f172a" stroke-width="1" pointer-events="none" visibility="hidden"/></svg>
</div></div><details id="counts-detail"><summary>打点次数（所选 block 的保留记录）</summary>
<table><thead><tr><th>block/subblock</th><th>次数</th><th>语义路径</th></tr></thead><tbody id="counts-body"></tbody></table></details>
<details id="events-detail"><summary>原始绝对 cycle（所选 block，整数）</summary>
<table><thead><tr><th>block/subblock</th><th>序号</th><th>该点第几次</th><th>cycle</th><th>语义路径</th><th>处理量 / 上次打点间隔 / 导出时速率</th></tr></thead><tbody id="events-body"></tbody></table></details>
<script id="lane-data" type="application/json">{payload}</script>
<script>{Path(__file__).with_name('timeline.js').read_text()}</script></html>'''
    (folder / "semantic.html").write_text(page)
    (folder / "semantic.svg").write_text(drawing)
    with (folder / "semantic.jsonl").open("w") as out:
        for event in events:
            out.write(json.dumps(dict(meta, launch_id=folder.name, **event), ensure_ascii=False) + "\n")
    (folder / "counts.json").write_text(json.dumps(dict(counts=summary, warnings=warnings), indent=2))
    with trace_file(folder / 'trace.json') as emit:
        write_capture(emit, folder.name, 1, meta, events, warnings, clock_mhz)



def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="单次采集目录，或含多个 rank*-pid*-launch* 的父目录")
    parser.add_argument("--output", type=Path, help="批量汇总目录，默认 <父目录>/result")
    parser.add_argument("--source", type=Path, nargs="+", required=True, help="编译所用的语义打点源码")
    parser.add_argument("--clock-mhz", type=float, help="用户确认的 cycle 时钟频率（MHz），用于 µs 刻度")
    parser.add_argument("--cycle-range", type=int, nargs=2, metavar=("START", "END"),
                        help="相对共同 origin 的 cycle 窗口；HTML 初始视图和 SVG 导出范围")
    args = parser.parse_args()
    mapping = event_map(args.source)
    if not (args.capture / "capture.json").is_file():
        from .batch import export_batch
        failed = export_batch(args.capture, args.output or args.capture / "result", mapping,
                              args.source, args.clock_mhz, args.cycle_range)
        raise SystemExit(1 if failed else 0)
    if args.output:
        parser.error("--output 仅用于父目录批量分析")
    meta, events, warnings = decode_capture(args.capture, mapping)
    render(args.capture, meta, events, warnings, args.clock_mhz, args.cycle_range)
    print(f"已导出 {len(events)} 个原始事件；{len(warnings)} 个丢弃告警；{args.capture / 'semantic.html'}")


if __name__ == "__main__":
    main()
