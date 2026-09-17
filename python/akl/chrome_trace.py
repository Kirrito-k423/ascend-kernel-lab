"""流式 Chrome Trace Event JSON；每次采集使用独立进程轨道和共同 cycle 起点。"""
import json
from collections import defaultdict
from contextlib import contextmanager
from itertools import groupby


def interval_work(lane, index):
    work = lane[index].get('work')
    if work and 'scope' in work: return work  # 累计计数归属当前观测；区间由 start/end_tick 明示。
    work = lane[index+1].get('work') if index+1 < len(lane) else None
    return work if work and 'scope' not in work else None


@contextmanager
def trace_file(path):
    with path.open('w', encoding='utf-8') as output:
        output.write('{"traceEvents":[')
        separator = ''

        def emit(event):
            nonlocal separator
            output.write(separator + json.dumps(event, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
            separator = ',\n'

        yield emit
        output.write('],"displayTimeUnit":"ns"}')  # ts/dur 始终是 µs，ns 仅控制查看器显示精度。


def write_capture(emit, capture_id, pid, meta, events, warnings, clock_mhz=None):
    rate = 1 / clock_mhz if clock_mhz else 0.001
    origin = min(int(event['tick']) for event in events)
    emit(dict(ph='M', name='process_name', pid=pid, args=dict(
        name=f"rank {meta['rank']} / device {meta['device']} / {capture_id}")))
    emit(dict(ph='i', s='p', name='capture (independent clock)', pid=pid, tid=0, ts=0,
              args=dict(meta, capture_id=capture_id, origin_cycle=str(origin), cycle_us=rate,
                        conversion='user MHz' if clock_mhz else 'default display scale', warnings=warnings)))
    lanes = defaultdict(list)
    for event in events:
        lanes[(event['block'], event['subblock'])].append(event)
    for tid, ((block, subblock), lane) in enumerate(sorted(lanes.items()), 1):
        emit(dict(ph='M', name='thread_name', pid=pid, tid=tid,
                  args=dict(name=f'block {block} / subblock {subblock}')))
        slices = []
        for level in range(max(len(event['path']) for event in lane)):
            # 父路径只合并连续区间，叶子逐次保留；不猜测字符串里的 begin/end。
            def key(item):
                index, event = item
                return tuple(event['path'][:level+1]), index if level >= len(event['path'])-1 else None
            for (prefix, _), group in groupby(enumerate(lane), key=key):
                indices = [index for index, _ in group]
                if len(prefix) <= level:
                    continue
                first, last = indices[0], indices[-1]
                event = lane[first]
                start, end = int(event['tick']), int(lane[min(last+1, len(lane)-1)]['tick'])
                # uint64 先做整数相减，避免绝对 tick 转浮点后丢失短区间。
                record = dict(name=prefix[-1], cat='debugclock', pid=pid, tid=tid,
                              ts=(start-origin)*rate, args=dict(event, path=list(prefix), level=level,
                              leaf=level == len(event['path'])-1, end_tick=str(end), duration_cycle=str(end-start),
                              last_sequence=lane[last]['sequence']))
                record['args']['point_work'] = event.get('work')
                record['args']['work'] = interval_work(lane, first) if first == last and record['args']['leaf'] else None
                record.update(dict(ph='X', dur=(end-start)*rate) if end > start else dict(ph='i', s='t'))
                slices.append((start, -end, level, record))
        # Complete 事件须先外后内；同刻度按层级排序，不产生交叉嵌套。
        for _, _, _, record in sorted(slices, key=lambda item: item[:3]):
            emit(record)
