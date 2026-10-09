#!/usr/bin/env python3
"""双端原始结果验收后输出脱敏网站 JSON；缺失另一端不生成性能结论。"""
import argparse,hashlib,json,math,statistics
from pathlib import Path

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def require(test,message):
    if not test:raise ValueError(message)
def p95(xs):
    a=sorted(xs);return a[math.ceil(.95*len(a))-1]
def analyse(runs,fingerprints):
    pairs={};outputs=[]
    receipt={}
    for line in fingerprints.read_text().splitlines():
        digest,name=line.split(maxsplit=1);receipt[name.strip()]=digest
    require('build/akl_network' in receipt and 'lib/libshmem.so' in receipt,'构建凭据不完整')
    for name in ['main.cpp','kernel.cpp','shared_cq_completion.h','CMakeLists.txt','run_pair.py','import_results.py']:
        require(receipt.get('src/'+name)==sha(Path(__file__).parent/name),'当前源码与编译凭据不一致')
    for path in runs:
        m=json.loads((path/'manifest.json').read_text())
        require(m.get('completion_impl') in ['sdk','cq-grouped'] and
                (m['engine']=='urma' or m['completion_impl']=='sdk'),'完成方式缺失或非法')
        require(m['status'] in ['validated','local_rank_validated'],'未验收/失败任务')
        require(m['binary_sha256']==receipt['build/akl_network'],'binary 哈希不一致')
        require(m.get('shmem_library_sha256')=={k[4:]:v for k,v in receipt.items() if k.startswith('lib/')},
                'SHMEM 完整动态库与构建凭据不一致')
        require(sha(path/'plan.csv')==m['plan_sha256'],'plan 哈希不一致')
        key=m['session'];pair=pairs.setdefault(key,{'metadata':m,'raw':{},'plan':(path/'plan.csv').read_text()})
        require(all(pair['metadata'][k]==m[k] for k in ['binary_sha256','plan_sha256','engine','completion_impl','placement','warmup','samples','topology_evidence_sha256','shmem_library_sha256']),
                '同一 session 两端配置不一致')
        if m['placement'] in ['same_cabinet','cross_cabinet']:
            topo=path/'topology-evidence.json'
            require(topo.is_file() and sha(topo)==m['topology_evidence_sha256'],'拓扑证据未绑定')
            t=json.loads(topo.read_text());pe=t.get('endpoints',[])
            require(t.get('mapping_confirmed') is True and len(pe)==2 and t.get('source') and t.get('path'),
                    '拓扑缺少确认来源、路径或两端')
            racks=[v.get('cabinet') for v in pe]
            require(all(racks) and (racks[0]==racks[1])==(m['placement']=='same_cabinet'),'机柜分类与映射不符')
        for rank in m['ranks']:
            require(rank in [0,1] and rank not in pair['raw'],'rank 重复或非法')
            raw=path/f'rank{rank}.jsonl'
            require(sha(raw)==m['output_sha256'][raw.name],'原始结果哈希不符')
            rows=[json.loads(line) for line in raw.read_text().splitlines()]
            require(all(r['rank']==rank and r['mismatches']==0 for r in rows),'oracle / rank 失败')
            pair['raw'][rank]=rows
    for trial,pair in pairs.items():
        require(set(pair['raw'])=={0,1},'缺少另一端原始结果，不能验收带宽')
        m=pair['metadata'];plan={}
        for line in pair['plan'].splitlines():
            if line and not line.startswith('#'):
                vals=[int(v) for v in line.split(',')];require(len(vals)==9,'计划格式错误')
                require(vals[0] not in plan,'计划 id 重复');plan[vals[0]]=vals
        expected={(cid,s) for cid in plan for s in range(-m['warmup'],m['samples'])}
        indexed=[]
        for rank in [0,1]:
            d={(r['case_id'],r['sample']):r for r in pair['raw'][rank]}
            require(set(d)==expected and len(d)==len(pair['raw'][rank]),'样本覆盖不完整或重复')
            indexed.append(d)
        for cid,vals in plan.items():
            _,get,cores,requested,batch,qps,workset,target,control=vals
            samples=[];warm=[];ticks=[];host=[]
            reference=None
            for s in range(-m['warmup'],m['samples']):
                a,b=indexed[0][cid,s],indexed[1][cid,s]
                fields=['engine','operation','requested_bytes','message_bytes','cores','batch','qps','configured_qps',
                        'ring_bytes','slots','operations','moved_bytes','control','soc','completion_impl']
                require(all(a[k]==b[k] for k in fields),'双端实际配置不同')
                require((a['cores'],a['requested_bytes'],a['batch'],a['qps'],a['control'])==(cores,requested,batch,qps,control),
                        '实际参数与计划不同')
                require(a['engine']==m['engine'] and a['operation']==('get' if get else 'put'),'接口/方向不符')
                require(a['completion_impl']==m['completion_impl'],'实际完成方式与 manifest 不符')
                require(a['event_ms']>0 and b['event_ms']==0 and not b['ticks'],'无发起方有效完成计时')
                require(len(a['ticks'])==cores*4,'核记录数量错误')
                for k in range(cores):
                    start,end,core,magic=map(int,a['ticks'][k*4:k*4+4])
                    require(end>start and core==k and magic==0x414b4c4e455431,'核计时未提交')
                part=((requested+cores-1)//cores+31)//32*32
                actual=part*cores;slots=max(batch,(max(workset,actual)+actual-1)//actual)
                ops=max(slots,(target+actual-1)//actual);ops=(ops+batch-1)//batch*batch
                require((a['message_bytes'],a['slots'],a['ring_bytes'],a['operations'],a['moved_bytes'])==
                        (actual,slots,actual*slots,ops,0 if control else ops*actual),'有效字节/循环口径错误')
                reference=a
                if s<0:warm.append(a['event_ms'])
                else:samples.append(a['event_ms']);host.append(a['host_launch_sync_us']);ticks.append(a['ticks'])
            med=statistics.median(samples);r=reference
            outputs.append(dict(id=hashlib.sha256((trial+'/'+str(cid)).encode()).hexdigest()[:16],
                placement=m['placement'],engine=r['engine'],operation=r['operation'],cores=cores,batch=batch,qps=qps,
                configuredQps=r['configured_qps'],completionImpl=r['completion_impl'],requestedBytes=requested,messageBytes=r['message_bytes'],
                ringBytes=r['ring_bytes'],requestedWorksetBytes=workset,operations=r['operations'],movedBytes=r['moved_bytes'],control=bool(control),
                eventMs=samples,warmupEventMs=warm,hostLaunchSyncUs=host,
                p50Us=med*1000,p95Us=p95(samples)*1000,gbps=r['moved_bytes']/med/1e6,
                coreTickDeltas=[[str(int(v[k*4+1])-int(v[k*4])) for k in range(cores)] for v in ticks],
                soc=r['soc'],binarySha256=m['binary_sha256'],planSha256=m['plan_sha256'],
                correctness='both endpoints validated every launch',fabricExclusive=False))
    require(outputs,'没有完整双端结果')
    return dict(schema='akl.network.public.v1',status='partial_measured',rows=outputs,
                evidence=dict(rawTimedSamples=sum(len(r['eventMs']) for r in outputs),
                    libSha256=receipt['lib/libshmem.so'],sourceSha256={k:v for k,v in receipt.items() if k.startswith('src/')},
                    endpointOracle='entire source, destination and 128B guards on both endpoints',
                    timing='initiator ACL Event through quiet completion; not sum of rank peaks',
                    missingTopologies=[k for k in ['same_cabinet','cross_cabinet'] if not any(r['placement']==k for r in outputs)]))
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runs',type=Path,nargs='+',required=True)
    p.add_argument('--fingerprints',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();result=analyse(a.runs,a.fingerprints)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(len(result['rows']),'verified configurations')
if __name__=='__main__':main()
