// 坐标只用相对差值转 Number；时间窗口及绝对 cycle 始终保留 BigInt。
const timeline = document.getElementById('timeline'), cursor = document.getElementById('cursor');
const origin = BigInt(timeline.dataset.origin), extent = BigInt(timeline.dataset.extent);
const mhz = Number(timeline.dataset.mhz) || null, maxDepth = Number(document.getElementById('depth').max);
let left = BigInt(timeline.dataset.left), right = BigInt(timeline.dataset.right), depth = maxDepth;
let pinned = false, cursorTick = null, drag = null;
const segments = [...timeline.querySelectorAll('[data-start]')].map(g => ({
    g, start: BigInt(g.dataset.start), end: BigInt(g.dataset.end),
    rect: g.querySelector('rect'), label: g.querySelector('text')
}));
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
        (mhz?' · Δµs≈'+(Number(cursorTick)/mhz).toFixed(3):'');
}
function draw() {
    let ruler='<rect width="1180" height="40" fill="white"/><text x="8" y="16">Δcycle</text>';
    if(mhz) ruler+='<text x="8" y="32">µs</text>';
    const ticks = [...new Set(Array.from({length:6},(_,i)=>left+(right-left)*BigInt(i)/5n))];
    document.querySelector('#grid').innerHTML = ticks.map(t =>
        `<line x1="${xAt(t)}" x2="${xAt(t)}" y1="0" y2="100%" stroke="#cbd5e1" stroke-dasharray="2 3"/>`).join('');
    for(const t of ticks) {
        const x=xAt(t), anchor=t===left?'start':t===right?'end':'middle';
        ruler+=`<path d="M${x},35 v5" stroke="#64748b"/><text x="${x}" y="16" text-anchor="${anchor}">${t}</text>`;
        if(mhz) ruler+=`<text x="${x}" y="32" text-anchor="${anchor}">${(Number(t)/mhz).toFixed(3)}</text>`;
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
    timeline.querySelectorAll('.lane').forEach((r,b)=>r.setAttribute('transform',`translate(0,${8+b*(depth*16+8)})`));
    timeline.setAttribute('viewBox',`0 0 1180 ${8+timeline.querySelectorAll('.lane').length*(depth*16+8)}`);
    draw();
};
draw();
