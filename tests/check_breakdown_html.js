const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2], 'utf8');
const payload = html.match(/id="breakdown-data">(.*?)<\/script>/s)[1], data = JSON.parse(payload);
const script = [...html.matchAll(/<script>(.*?)<\/script>/sg)].map(m=>m[1]).find(s=>s.includes("const data=JSON.parse"));
const cell=()=>({textContent:'',style:{}});
const select={selectedIndex:0, options:[], add(o){this.options.push(o);}, addEventListener(_,fn){this.change=fn;}};
const body={rows:[], replaceChildren(){this.rows=[];}, insertRow(){const row={cells:[],insertCell(){const c=cell();this.cells.push(c);return c;}};this.rows.push(row);return row;}};
const image=()=>({attrs:{},setAttribute(k,v){this.attrs[k]=v;}});
const svg=()=>({...image(),image:image(),querySelector(){return this.image;}});
const elements={'breakdown-data':{textContent:payload},'breakdown-core':select,'breakdown-values':body,
    'breakdown-status':cell(),'breakdown-bar':svg(),'breakdown-pie':svg()};
vm.runInNewContext(script,{document:{getElementById:id=>elements[id]},Option: function(text){this.text=text;}});
assert.equal(select.options.length,data.panels.length);
for(let i=0;i<data.panels.length;i++) {
    select.selectedIndex=i; select.change();
    assert.equal(body.rows.length,data.paths.length);
    assert.equal(elements['breakdown-pie'].attrs.viewBox,data.panels[i].box.join(' '));
    const [x,y,w,h]=data.panels[i].box;
    assert(x>=0 && y>=0 && x+w<=data.width && y+h<=data.height);
    assert.equal(elements['breakdown-bar'].image.attrs.width,data.width);
    assert(elements['breakdown-status'].textContent.includes(data.panels[i].label));
}
console.log(`PASS: ${data.panels.length} panel switches, PNG bounds, table rows and labels`);
