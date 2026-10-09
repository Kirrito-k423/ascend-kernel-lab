#!/usr/bin/env python3
"""单变量消息曲线 + 并发扫描；MTE 的 batch=1 为 scratch 完成复用边界。"""
import argparse,random
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--engine',choices=['mte','urma'],required=True)
p.add_argument('--smoke',action='store_true');p.add_argument('--max-cores',type=int,default=64)
p.add_argument('--seed',type=int,default=20261009);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();rows=[]
sizes=[32,4096,1048576] if a.smoke else [2**k for k in range(5,25)]
for get in [0,1]:
 for size in sizes:
  cores=1
  for batch in ([1] if a.engine=='mte' else [1,32]):
   if size*batch>128*2**20:continue
   workset=size*batch if size<131072 else 64*2**20
   target=size*128 if a.smoke else max(size*8192 if size<131072 else 256*2**20,workset)
   rows.append([get,cores,size,batch,1,workset,target,0])
 if not a.smoke:
  for size in [16384,1048576,16777216]:
   if a.engine=='mte':
    for cores in [2,4,8,16,32,48,64]:
     if cores<=a.max_cores:rows.append([get,cores,size,1,1,64*2**20,256*2**20,0])
   else:
    for qp in [1,2,4,8]:
     for batch in [1,8,32,128]:
      if size*batch<=128*2**20 and (qp,batch) not in [(1,1),(1,32)]:rows.append([get,1,size,batch,qp,64*2**20,256*2**20,0])
# 空对照，不从正式计时扣减。
rows.extend([[get,1,4096,1,1,4096,4096*8192,1] for get in [0,1]])
random.Random(a.seed).shuffle(rows)
a.output.parent.mkdir(parents=True,exist_ok=True)
a.output.write_text('# id,get,cores,requested_message_bytes,batch,qps,workset_bytes,target_bytes,control\n'+
 '\n'.join(','.join(map(str,[i,*row])) for i,row in enumerate(rows))+'\n')
print(len(rows),'configurations')
