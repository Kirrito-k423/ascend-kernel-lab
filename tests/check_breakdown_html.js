const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2], 'utf8');
const payload = html.match(/id="breakdown-data">(.*?)<\/script>/s)[1], data = JSON.parse(payload);
const script = [...html.matchAll(/<script>(.*?)<\/script>/sg)].map(m=>m[1]).find(s=>s.includes("const data=JSON.parse"));
const cell=()=>({textContent:'',innerHTML:''});
const select={selectedIndex:0, options:[], add(o){this.options.push(o);}, addEventListener(_,fn){this.change=fn;}};
const body={rows:[], replaceChildren(){this.rows=[];}, insertRow(){const row={cells:[],insertCell(){const c=cell();this.cells.push(c);return c;}};this.rows.push(row);return row;}};
const elements={'breakdown-data':{textContent:payload},'breakdown-core':select,'breakdown-values':body};
for(const name of ['title','status','bar','pie','save-bar','save-pie','mean-bar','mean-pie']) elements['breakdown-'+name]=cell();
vm.runInNewContext(script,{document:{getElementById:id=>elements[id]},Option: function(text){this.text=text;}});
assert.equal(select.options.length,data.panels.length);
assert.equal(elements['breakdown-mean-pie'].download,`rank${data.rank}-mean-pie.png`);
for(let i=0;i<data.panels.length;i++) {
    select.selectedIndex=i; select.change();
    assert.equal(body.rows.length,data.paths.length);
    const ordered=data.panels[i].totals.map((v,j)=>({v:Number(v),path:data.paths[j]})).sort((a,b)=>b.v-a.v);
    assert.deepEqual(body.rows.map(r=>r.cells[0].textContent),ordered.map(r=>r.path));
    for(const [j,kind] of ['bar','pie'].entries()) {
        assert.equal(elements['breakdown-'+kind].innerHTML,data.panels[i].svg[j]);
        assert(data.panels[i].svg[j].includes('Rank '+data.rank+' | '+data.panels[i].label));
    }
    assert(elements['breakdown-status'].textContent.includes(`Rank ${data.rank} · ${data.panels[i].label}`));
}
console.log(`PASS: ${data.panels.length} rank-isolated panels, descending tables and embedded charts`);
