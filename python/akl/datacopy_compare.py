"""为已验证单位、边界和精确 shape 的 trace 添加独立经验基线轨道。"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from .datacopy import CopyCase


def annotate(trace, sidecar, catalog, trace_sha256):
    if sidecar.get('schema') != 'akl.datacopy.shapes.v1' or catalog.get('schema') != 'akl.datacopy.catalog.v1':
        raise ValueError('未知 shape/catalog 协议')
    if sidecar.get('trace_sha256') != trace_sha256: raise ValueError('shape sidecar 不属于这份 trace')
    result = copy.deepcopy(trace)
    events = result['traceEvents']
    if not isinstance(events, list): raise ValueError('traceEvents 必须是数组')
    verified = trace.get('akl_clock', {})
    default_scale_pids = {e.get('pid') for e in events if e.get('args', {}).get('conversion') == 'default display scale'}
    lanes, report, seen = {}, [], set()
    # string tid 不会与原有数值轨道碰撞；若已有同名轨道则继续加后缀。
    used = {(e.get('pid'), e.get('tid')) for e in events}
    for item in sidecar.get('operations', []):
        index = item.get('event_index')
        if type(index) is not int or not 0 <= index < len(trace['traceEvents']) or index in seen:
            raise ValueError('event_index 越界或重复')
        seen.add(index)
        actual = trace['traceEvents'][index]
        if actual.get('ph') != 'X' or not all(isinstance(actual.get(k),(int,float)) and not isinstance(actual.get(k),bool) and math.isfinite(actual[k]) and actual[k]>=0 for k in ('ts','dur')):
            raise ValueError('目标必须为有效完整区间')
        lane = (actual.get('pid'),actual.get('tid'))
        if lane not in lanes:
            tid=f"akl-baseline-{lane[1]}"
            while (lane[0],tid) in used: tid+='-new'
            used.add((lane[0],tid));lanes[lane]=tid
            events.append(dict(ph='M',name='thread_name',pid=lane[0],tid=tid,args=dict(name=f"{lane[1]} · DataCopy 无竞争参考")))
        reason = None
        match = None
        environment = sidecar.get('environment')
        if (verified.get('unit') != 'us' or verified.get('verified') is not True or not environment
                or verified.get('frequency_hz') != environment.get('clock_hz') or actual.get('pid') in default_scale_pids):
            reason = '未验证 trace 的实际时间单位/芯片时钟，不能画参考长度'
        elif item.get('scope') != 'datacopy_loop' or item.get('boundary') != 'completion':
            reason = '计时范围不是相同同步方式下的纯 DataCopy 循环'
        elif not item.get('case'):
            reason = '缺少 API/shape/dtype/同步/工作集记录'
        else:
            case=CopyCase(**item['case'])
            signature=case.signature()
            if case.control != 'payload':
                reason='对照实验不是业务 DataCopy'
            elif item.get('call_count') != case.loops*case.batch:
                reason='调用数量与基准测量边界不一致'
            else:
                key=dict(environment=environment,case=signature)
                matches=[e for e in catalog['entries'] if e['key']==key]
                if len(matches)!=1: reason='没有唯一的精确环境/shape基线；不外推、不选择最快项'
                else: match=matches[0]
        record=dict(event_index=index, name=actual['name'], status='unmatched', reason=reason)
        event=dict(pid=lane[0],tid=lanes[lane],ts=actual['ts'],cat='akl_empirical_reference')
        if match:
            if match.get('baseline_kind')!='empirical_isolated_completion' or any(
                    not isinstance(match.get(k),(float,int)) or not math.isfinite(match[k]) or match[k]<=0
                    for k in ('p50_us_per_call','p95_us_per_call')):
                raise ValueError('基线数值或来源类型无效')
            expected=match['p50_us_per_call']*item['call_count']
            p95=match['p95_us_per_call']*item['call_count']
            record.update(status='matched',reason=None,baseline_id=match['id'],expected_p50_us=expected,
                          expected_p95_us=p95,actual_us=actual['dur'],actual_over_reference=actual['dur']/expected)
            event.update(ph='X',name='DataCopy 经验参考 p50（非理论极限）',dur=expected,
                         args=dict(record,baseline=match['evidence']))
        else:
            event.update(ph='i',s='t',name='DataCopy 参考耗时 ? ',args=record)
        events.append(event);report.append(record)
    return result, report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace',type=Path);parser.add_argument('--shapes',type=Path,required=True)
    parser.add_argument('--catalog',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args()
    if a.output.exists(): parser.error('输出已存在，拒绝覆盖')
    raw=a.trace.read_bytes()
    result,report=annotate(json.loads(raw),json.loads(a.shapes.read_text()),json.loads(a.catalog.read_text()),hashlib.sha256(raw).hexdigest())
    a.output.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
    a.output.with_suffix('.comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(f"匹配 {sum(r['status']=='matched' for r in report)}/{len(report)}；未匹配项只画 ? 瞬时标记")

if __name__ == '__main__': main()
