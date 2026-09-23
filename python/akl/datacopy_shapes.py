"""解析真实算子的 DataCopy shape 旁路采集；不把发射观测当成DMA计时。"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from .semantic import decode_capture, event_map

MAGIC=0x414b4c4350593031
SLOTS=24
RECORD_WORDS=20
DTYPES={0:'unknown',1:'float16',2:'bfloat16',3:'float32',4:'int32',5:'uint32',6:'uint8'}
APIS={0:'DataCopy_count',1:'DataCopy_params',2:'DataCopyPad_params'}
SITES={1:'TokenToExpert.token_load',2:'TokenToExpert.weights_load',3:'TokenToExpert.token_store'}


def bits(mask,step=1):
    return [i*step for i in range(64) if mask & (1<<i)]


def decode_shapes(raw, meta):
    if meta.get('schema')!='akl.datacopy.capture.v1' or meta.get('slots')!=SLOTS or meta.get('record_words')!=RECORD_WORDS:
        raise ValueError('未知 shape ABI')
    blocks=meta.get('blocks');words=8+SLOTS*RECORD_WORDS
    if type(blocks) is not int or blocks<=0 or len(raw)!=blocks*words*8:
        raise ValueError('shape buffer长度不匹配')
    result=[];warnings=[]
    for block,row in enumerate(struct.iter_unpack(f'<{words}Q',raw)):
        if row[0]!=MAGIC or row[1]!=1 or row[2]!=SLOTS or row[4]!=block or row[6]!=RECORD_WORDS or row[7]!=1:
            raise ValueError(f'block {block} shape未提交或损坏')
        if row[3]:warnings.append(f'block {block}: dropped calls={row[3]}，覆盖不完整')
        for slot in range(SLOTS):
            r=row[8+slot*RECORD_WORDS:8+(slot+1)*RECORD_WORDS]
            if not r[11]:continue
            site,scene,anchor,api,direction,dtype,element,blocks_,length,src,dst=r[:11]
            if api not in APIS or direction not in (0,1) or dtype not in DTYPES or element not in (1,2,4,8) or not blocks_ or not length:
                raise ValueError('shape字段无效')
            if r[12]:warnings.append(f'block {block} slot {slot}: shape changed {r[12]} calls')
            payload=length*element if api==0 else blocks_*length*(32 if api==1 else 1)
            if payload!=r[16]:raise ValueError('payload计算不一致')
            block_bytes=length*element if api==0 else length*(32 if api==1 else 1)
            gm_gap=0 if api==0 else (dst if direction else src)*(32 if api==1 else 1)
            ub_gap=0 if api==0 else (src if direction else dst)*32
            result.append(dict(block=block,subblock=row[5],slot=slot,site_id=site,site=SITES.get(site,f'unknown_site_{site}'),
                scene=scene,group=scene-1 if scene else None,anchor_sequence=anchor,api=APIS[api],
                direction='UB_GM' if direction else 'GM_UB',dtype=DTYPES[dtype],element_bytes=element,
                block_bytes=block_bytes,blocks=blocks_,gm_gap_bytes=gm_gap,ub_gap_bytes=ub_gap,
                raw_length=length,raw_src_stride=src,raw_dst_stride=dst,
                payload_bytes_per_call=payload,calls=r[11],total_payload_bytes=str(payload*r[11]),
                gm_offsets_mod32=bits(r[13]),gm_32B_bins_mod512=bits(r[14],32),ub_offsets_mod32=bits(r[15]),
                shape_changed_calls=r[12],valid_shape=(r[12]==0 and dtype!=0),
                boundary='shape_observation_only',expected_us=None))
    return result,warnings


def convert(folder, sources, output):
    meta=json.loads((folder/'datacopy-shapes.json').read_text())
    raw=(folder/'datacopy-shapes.bin').read_bytes()
    rows,warnings=decode_shapes(raw,meta)
    source_hashes={hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    recorded_source=meta.get('source_sha256')
    if recorded_source and recorded_source!='unknown' and recorded_source not in source_hashes:
        raise ValueError('提供的source不属于采集时编译的源码')
    if not recorded_source or recorded_source=='unknown':
        warnings.append('采集未带编译源码SHA256，anchor映射未证明与编译版本一致')
    trace_meta,events,trace_warnings=decode_capture(folder,event_map(sources))
    if trace_meta['blocks']!=meta['blocks'] or trace_meta['rank']!=meta['rank']:
        raise ValueError('shape与trace不属于同一采集')
    index={(e['block'],e['sequence']):e for e in events}
    for r in rows:
        event=index.get((r['block'],r['anchor_sequence']))
        r['anchor_path']=event['path'] if event else None
        r['anchor_tick']=event['tick'] if event else None
        if not event:
            r['valid_shape']=False;warnings.append(f"block {r['block']} slot {r['slot']}: missing trace anchor")
    result=dict(schema='akl.datacopy.observations.v1',environment=meta,observations=rows,
                warnings=warnings+trace_warnings,shape_raw_sha256=hashlib.sha256(raw).hexdigest(),
                source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
                note='自动采集实际shape及调用数；无DMA独立完成时间，工作集/缓存/竞争仍未知，不生成理想耗时')
    output.mkdir(parents=True,exist_ok=False)
    (output/'observations.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    # 生成候选micro case；未知dtype、对齐分布或UB跨步不能静默转换。
    candidates=[];unsupported=[];seen=set()
    from .datacopy import CopyCase
    for r in rows:
        if not r['valid_shape'] or r['ub_gap_bytes'] or not r['gm_offsets_mod32']:
            unsupported.append(dict(site=r['site'],reason='缺失/变化shape或暂不支持的UB布局'));continue
        for offset in r['gm_offsets_mod32']:
            spec=dict(direction=r['direction'],api=r['api'],dtype=r['dtype'],block_bytes=r['block_bytes'],
                      blocks=r['blocks'],gm_gap_bytes=r['gm_gap_bytes'],gm_offset_bytes=offset)
            key=json.dumps(spec,sort_keys=True)
            if key in seen:continue
            seen.add(key)
            name=f'capture_shape_{len(candidates)}'
            try:
                c=CopyCase(name,**spec);c.params()
            except ValueError as error:
                unsupported.append(dict(site=r['site'],spec=spec,reason=str(error)));continue
            from dataclasses import asdict
            candidates.append(asdict(c))
    (output/'candidate-cases.json').write_text(json.dumps(candidates,ensure_ascii=False,indent=2))
    (output/'unsupported.json').write_text(json.dumps(unsupported,ensure_ascii=False,indent=2))
    lines=['# 自动采集的 DataCopy shape','',f"SoC: {meta['soc']}；CANN: {meta['cann_version']}；h={meta['h']}，k={meta['k']}，tokens={meta['tokens']}。",'',
           'shape候选只决定下一轮实验输入；默认候选为单AIV串行完成、小工作集，尚不等于生产场景。不支持的dtype/布局保留在unsupported中，不替换为其他dtype。候选仍需在目标芯片编译和验证。', '',
           '| 核 | group | 调用位置 | API / dtype | 有效B/次 | 次数 | 参考μs |','|---|---|---|---|---:|---:|---|']
    lines += [f"| {r['block']} | {r['group']} | {r['site']} | {r['api']} / {r['dtype']} | {r['payload_bytes_per_call']} | {r['calls']} | ? |" for r in rows]
    lines += ['', '告警：']+[f'- {w}' for w in warnings+trace_warnings]
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('capture',type=Path)
    p.add_argument('--source',type=Path,action='append',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=convert(a.capture,a.source,a.output);print(f"导出 {len(r['observations'])} 个调用位置/核/group shape，{len(r['warnings'])} 条告警")

if __name__=='__main__':main()
