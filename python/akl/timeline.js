// 坐标只用相对差值转 Number；时间窗口及绝对 cycle 始终保留 BigInt。
const timeline = document.getElementById('timeline'), cursor = document.getElementById('cursor');
const origin = BigInt(timeline.dataset.origin), extent = BigInt(timeline.dataset.extent);
const maxDepth = Number(document.getElementById('depth').max);
const laneData = JSON.parse(document.getElementById('lane-data').textContent);
let cycleUs = Number(document.getElementById('cycle-us').value), unit = 'cycle';
let left = BigInt(timeline.dataset.left), right = BigInt(timeline.dataset.right), depth = maxDepth;
let pinned = false, cursorTick = null, drag = null, selected = [], segments = [];
const formatUs = ticks => (Number(ticks)*cycleUs).toLocaleString('en-US',{useGrouping:false,maximumSignificantDigits:12});
function tables() {
    for(const [id,key] of [['counts','counts'],['events','rows']])
        document.getElementById(id+'-body').innerHTML = document.getElementById(id+'-detail').open ? selected.map(b=>laneData[b][key]).join('') : '';
}
function layout() {
    timeline.querySelectorAll('.lane').forEach((g,i)=>g.setAttribute('transform',`translate(0,${8+i*(depth*16+8)})`));
    timeline.setAttribute('viewBox',`0 0 1180 ${8+selected.length*(depth*16+8)}`);
}
function selectBlocks() {
    try {
        const blocks = new Set();
        for(const part of document.getElementById('blocks').value.split(',')) {
            const match = part.trim().match(/^(\d+)(?:-(\d+))?$/);
            if(!match) throw Error();
            const a=Number(match[1]), b=Number(match[2]??match[1]);
            if(a>b || b>=laneData.length) throw Error();
            for(let n=a;n<=b;n++) blocks.add(n);
        }
        selected=[...blocks].sort((a,b)=>a-b);
        document.getElementById('lanes').innerHTML=selected.map(b=>`<g class="lane" data-block="${b}">${laneData[b].svg}</g>`).join('');
        segments=[...timeline.querySelectorAll('[data-start]')].map(g=>({
            g,start:BigInt(g.dataset.start),end:BigInt(g.dataset.end),rect:g.querySelector('rect'),label:g.querySelector('text'),
            title:g.querySelector('title'),base:g.querySelector('title').textContent.replace(/ \| Δµs=.*$/,'')
        }));
        layout(); tables(); draw();
        document.getElementById('block-status').textContent=`已绘制 ${selected.length} / ${laneData.length} 个 block`;
    } catch {document.getElementById('block-status').textContent=`请输入 0…${laneData.length-1} 内的编号或范围，例如 0-7,16`;}
}
const selection = document.getElementById('selection');
const min = (a,b) => a < b ? a : b, max = (a,b) => a > b ? a : b;
const xAt = tick => 100 + 1040 * Number(tick-left) / Number(right-left);
function fraction(e) {
    const p = new DOMPoint(e.clientX,e.clientY).matrixTransform(timeline.getScreenCTM().inverse());
    return Math.max(0,Math.min(1,(p.x-100)/1040));
}
function tickAt(f, a=left, b=right) { return a+(b-a)*BigInt(Math.round(f*1000000))/1000000n; }
function showCursor() {
    const visible = cursorTick !== null && cursorTick >= left && cursorTick <= right;
    cursor.setAttribute('visibility',visible?'visible':'hidden');
    if (cursorTick === null) return;
    cursor.setAttribute('x1',xAt(cursorTick)); cursor.setAttribute('x2',xAt(cursorTick));
    document.getElementById('readout').textContent=(pinned?'已固定':'鼠标估计')+
        ' · Δcycle≈'+cursorTick+' · cycle≈'+(origin+cursorTick)+
        ' · Δµs≈'+formatUs(cursorTick);
}
function draw() {
    let ruler=`<rect width="1180" height="40" fill="white"/><text x="8" y="16">${unit==='cycle'?'Δcycle':'Δµs'}</text>`;
    const ticks = [...new Set(Array.from({length:6},(_,i)=>left+(right-left)*BigInt(i)/5n))];
    document.querySelector('#grid').innerHTML = ticks.map(t =>
        `<line x1="${xAt(t)}" x2="${xAt(t)}" y1="0" y2="100%" stroke="#cbd5e1" stroke-dasharray="2 3"/>`).join('');
    for(const t of ticks) {
        const x=xAt(t), anchor=t===left?'start':t===right?'end':'middle';
        ruler+=`<path d="M${x},35 v5" stroke="#64748b"/><text x="${x}" y="16" text-anchor="${anchor}">${unit==='cycle'?t:formatUs(t)}</text>`;
    }
    document.getElementById('axis').innerHTML=ruler;
    for(const s of segments) {
        const visible=Number(s.g.dataset.level)<depth && (s.start===s.end ? s.start>=left && s.start<=right : s.end>left && s.start<right);
        s.g.style.display=visible?'':'none';
        if(!visible) continue;
        const x=xAt(max(s.start,left)), width=Math.max(1,xAt(min(s.end,right))-x);
        s.rect.setAttribute('x',x); s.rect.setAttribute('width',width);
        s.label.setAttribute('x',x+3);
        s.label.textContent=s.g.dataset.label.slice(0,Math.max(0,Math.floor(width/9)-1));
        s.title.textContent=s.base+' | Δµs='+formatUs(s.end-s.start);
    }
    document.getElementById('from').value=left; document.getElementById('to').value=right;
    document.getElementById('window').textContent='窗口 Δcycle '+left+'…'+right+'（跨度 '+(right-left)+'）';
    showCursor();
}
function view(a,b) {
    const span=min(extent,max(1n,b-a));
    left=max(0n,min(a,extent-span)); right=left+span; draw();
}
function zoom(factor,f=.5) {
    const anchor=tickAt(f), span=max(1n,(right-left)*BigInt(Math.round(factor*1000000))/1000000n);
    const start=anchor-span*BigInt(Math.round(f*1000000))/1000000n;
    view(start,start+span);
}
for(const [id,factor] of [['zoom-in',.5],['zoom-out',2]])
    document.getElementById(id).onclick=()=>zoom(factor);
for(const [id,direction] of [['pan-left',-1n],['pan-right',1n]])
    document.getElementById(id).onclick=()=>{const d=direction*max(1n,(right-left)/4n);view(left+d,right+d);};
document.getElementById('reset').onclick=()=>view(0n,extent);
document.getElementById('apply').onclick=()=>{
    try {
        const a=BigInt(document.getElementById('from').value), b=BigInt(document.getElementById('to').value);
        if(a<0n || b<=a || b>extent) throw Error();
        view(a,b);
    } catch {document.getElementById('window').textContent='请输入范围内的整数：0 ≤ 起点 < 终点 ≤ '+extent;}
};
timeline.addEventListener('wheel',e=>{
    if(!e.ctrlKey && !e.metaKey && !e.shiftKey) return;
    e.preventDefault();
    if(e.shiftKey) {const d=BigInt(Math.sign(e.deltaY || e.deltaX))*max(1n,(right-left)/10n);view(left+d,right+d);}
    else zoom(Math.exp(Math.max(-.7,Math.min(.7,e.deltaY*.002))),fraction(e));
},{passive:false});
timeline.addEventListener('pointerdown',e=>{
    if(e.button!==0) return;
    drag={f:fraction(e),a:left,b:right,x:e.clientX,pan:e.shiftKey,moved:false};
    timeline.setPointerCapture(e.pointerId);
});
timeline.addEventListener('pointermove',e=>{
    const f=fraction(e);
    if(drag) {
        drag.moved ||= Math.abs(e.clientX-drag.x)>4;
        if(drag.pan) {const d=tickAt(drag.f,drag.a,drag.b)-tickAt(f,drag.a,drag.b);view(drag.a+d,drag.b+d);}
        else {
            selection.setAttribute('x',100+1040*Math.min(f,drag.f));
            selection.setAttribute('width',1040*Math.abs(f-drag.f));
            selection.setAttribute('visibility','visible');
        }
    } else if(!pinned) {cursorTick=tickAt(f);showCursor();}
});
timeline.addEventListener('pointerup',e=>{
    if(!drag) return;
    const f=fraction(e), d=drag; drag=null;
    selection.setAttribute('visibility','hidden'); timeline.releasePointerCapture(e.pointerId);
    if(d.moved && !d.pan) view(tickAt(Math.min(f,d.f),d.a,d.b),tickAt(Math.max(f,d.f),d.a,d.b));
    else if(!d.moved) {pinned=!pinned;cursorTick=tickAt(f);showCursor();}
});
timeline.addEventListener('pointercancel',()=>{drag=null;selection.setAttribute('visibility','hidden');});
document.getElementById('depth').oninput=e=>{
    depth=Math.max(1,Math.min(maxDepth,Math.trunc(Number(e.target.value)||1)));
    layout(); draw();
};
document.getElementById('cycle-us').oninput=e=>{
    const value=Number(e.target.value);
    if(!Number.isFinite(value) || value<=0 || !Number.isFinite(Number(extent)*value)) {
        document.getElementById('conversion').textContent='请输入有限正数，已保留上次有效换算';return;
    }
    cycleUs=value;
    document.getElementById('conversion').textContent='当前显示换算：1 cycle = '+cycleUs+' µs';draw();
};
document.getElementById('unit').onchange=e=>{unit=e.target.value;draw();};
document.getElementById('apply-blocks').onclick=selectBlocks;
document.getElementById('all-blocks').onclick=()=>{document.getElementById('blocks').value='0-'+(laneData.length-1);selectBlocks();};
for(const id of ['counts','events']) document.getElementById(id+'-detail').ontoggle=tables;
selectBlocks();
