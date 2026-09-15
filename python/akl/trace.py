"""ABI校验与保留原始tick的事件解析；无NPU依赖。"""
from .cases import MAGIC, CAPACITY, WORDS

EVENT_NAMES = {0:"entry",1:"ready",2:"measure_begin",3:"measure_end",4:"output_complete"}

def decode(rows, run_id, launch_id, device, expected_counts):
    events=[]
    if len(rows)!=len(expected_counts): raise ValueError("通道数不匹配")
    for core,row in enumerate(rows):
        if len(row)!=WORDS or int(row[0])!=MAGIC or int(row[1])!=1 or int(row[7])!=1:
            raise ValueError(f"AIV {core} 记录未提交或ABI不匹配")
        count,dropped=int(row[2]),int(row[3])
        if count>CAPACITY or dropped: raise ValueError(f"AIV {core} 记录丢弃或容量异常")
        if int(row[4])!=core or int(row[6])!=expected_counts[core]:
            raise ValueError(f"AIV {core} 核编号或保留元素数量错误：实际 {int(row[6])}，预期 {expected_counts[core]}")
        previous=-1
        for seq in range(count):
            event_id,tick=int(row[8+seq*2]),int(row[9+seq*2])
            if tick<previous: raise ValueError("同核时间戳不单调")
            previous=tick
            events.append(dict(run_id=run_id,launch_id=launch_id,stream_id=0,rank=0,
                device_id=device,core_type="AIV",block_id=core,subblock_id=int(row[5]),
                clock_domain_id=f"device{device}_sys_cnt_unverified",tick=str(tick),
                sequence=seq,event_id=event_id,name=EVENT_NAMES.get(event_id,f"event_{event_id}"),
                span_id=0,kind="begin" if event_id==2 else "end" if event_id==3 else "instant",
                boundary="completion" if event_id in (3,4) else "observation",
                valid=True,invalid_reason=None))
        ids=[int(row[8+seq*2]) for seq in range(count)]
        if ids!=list(EVENT_NAMES): raise ValueError("实验缺少必要端点或事件顺序错误")
    return events

def duration_ticks(events, core):
    row={e["event_id"]:int(e["tick"]) for e in events if e["block_id"]==core}
    if 2 not in row or 3 not in row: raise ValueError("缺少计时端点")
    result=row[3]-row[2]
    if result<0: raise ValueError("负耗时")
    return result
