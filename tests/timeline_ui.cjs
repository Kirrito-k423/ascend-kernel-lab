// Node 原生 DOM 替身验证交互逻辑；真实布局另外在浏览器验收。
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const path = require('node:path');
function element(dataset={}) {
    return {dataset, style:{}, attrs:{}, value:'', textContent:'', innerHTML:'', handlers:{},
        setAttribute(k,v){this.attrs[k]=String(v);}, addEventListener(k,v){this.handlers[k]=v;}};
}
const names = ['timeline','cursor','selection','depth','axis','grid','readout','from','to','window',
    'zoom-in','zoom-out','pan-left','pan-right','reset','apply','lane-data','lanes','cycle-us','unit',
    'search','search-status','clear-search','blocks','block-status','apply-blocks','all-blocks','conversion','counts-body','counts-detail','events-body','events-detail'];
const ids=Object.fromEntries(names.map(id=>[id,element()]));
Object.assign(ids.timeline.dataset,{origin:String(2n**60n),extent:'1000',left:'0',right:'1000'});
ids.depth.max=4; ids['cycle-us'].value='0.001'; ids.blocks.value='0-7';
ids['lane-data'].textContent=JSON.stringify(Array.from({length:64},(_,i)=>({svg:'',rows:'row'+i,counts:'count'+i})));
let lanes=[], segments=[];
Object.defineProperty(ids.lanes,'innerHTML',{set(value){
    lanes=[]; segments=[];
    for(const match of value.matchAll(/data-block="(\d+)"/g)) {
        lanes.push(element({block:match[1]}));
        const g=element({start:'0',end:'10',label:'part',level:'3'});
        const children={rect:element(),text:element(),title:element()};
        children.title.textContent='part | Δcycle=10';
        g.querySelector=name=>children[name];segments.push(g);
    }
}});
ids.timeline.querySelectorAll=s=>s==='.lane'?lanes:segments;
ids.timeline.getScreenCTM=()=>({inverse:()=>null});
ids.timeline.setPointerCapture=()=>{};ids.timeline.releasePointerCapture=()=>{};
class DOMPoint {constructor(x,y){this.x=x;this.y=y;}matrixTransform(){return this;}}
const source=process.argv[2] || path.join(__dirname,'../python/akl/timeline.js');
vm.runInNewContext(fs.readFileSync(source,'utf8'),{document:{getElementById:id=>ids[id],querySelector:s=>ids[s.slice(1)]},DOMPoint});
assert.equal(lanes.length,8); assert.equal(ids['events-body'].innerHTML,'');
ids.unit.onchange({target:{value:'us'}});
assert.match(ids.axis.innerHTML,/>Δµs</);assert.match(ids.axis.innerHTML,/>1<\/text>/);
ids['cycle-us'].oninput({target:{value:'0.002'}});
assert.match(ids.axis.innerHTML,/>2<\/text>/);
assert.match(segments[0].querySelector('title').textContent,/Δµs=0.02$/);
ids['cycle-us'].oninput({target:{value:'0'}});
assert.match(ids.conversion.textContent,/有限正数/);
assert.match(ids.axis.innerHTML,/>2<\/text>/);
ids.blocks.value='63,2-3,2';ids['apply-blocks'].onclick();
assert.deepEqual(lanes.map(g=>g.dataset.block),['2','3','63']);
for(const value of ['64','-1','3-2','','1,,2']) {
    ids.blocks.value=value;ids['apply-blocks'].onclick();assert.equal(lanes.length,3);
    assert.match(ids['block-status'].textContent,/请输入/);
}
ids['events-detail'].open=true;ids['events-detail'].ontoggle();
assert.equal(ids['events-body'].innerHTML,'row2row3row63');
ids['events-detail'].open=false;ids['events-detail'].ontoggle();assert.equal(ids['events-body'].innerHTML,'');
ids.depth.oninput({target:{value:'2'}});assert.equal(ids.timeline.attrs.viewBox,'0 0 1180 128');
ids['zoom-in'].onclick();assert.equal(String(ids.from.value),'250');assert.equal(String(ids.to.value),'750');
ids.unit.onchange({target:{value:'cycle'}});assert.match(ids.axis.innerHTML,/>Δcycle</);
assert.equal(String(ids.from.value),'250');
ids['all-blocks'].onclick();assert.equal(lanes.length,64);assert.ok(segments.every(g=>g.style.display==='none'));
const pointer={clientX:360,clientY:10,pointerId:1,button:0};
ids.timeline.handlers.pointerdown(pointer);ids.timeline.handlers.pointerup(pointer);
assert.match(ids.readout.textContent,/cycle≈1152921504606847351/);
ids.reset.onclick();ids.blocks.value='2-3';ids['apply-blocks'].onclick();
ids.depth.oninput({target:{value:'4'}});
ids.search.value='PART';ids.search.oninput();assert.match(ids['search-status'].textContent,/匹配 2 /);
assert.ok(segments.every(g=>g.querySelector('rect').attrs['stroke-width']==='2'));
ids.search.value='missing';ids.search.oninput();assert.match(ids['search-status'].textContent,/匹配 0 /);
assert.ok(segments.every(g=>g.style.opacity==='.25'));
ids['clear-search'].onclick();assert.ok(segments.every(g=>g.style.opacity==='1'));
ids.search.value='part';ids.search.oninput();ids.depth.oninput({target:{value:'2'}});
assert.match(ids['search-status'].textContent,/匹配 0 /);
console.log('PASS: default 8/64 blocks, units/rate, invalid input, ranges/dedup, lazy tables, zoom/depth, uint64 cursor, search/highlight');
