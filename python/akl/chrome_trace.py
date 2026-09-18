"""流式 Chrome Trace Event JSON；每次采集使用独立进程轨道和共同 cycle 起点。"""
import json
from collections import defaultdict
from contextlib import contextmanager


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
        # 每条原始记录仅生成一个阶段，按同核相邻 tick 求差，不叠加父路径。
        for index, event in enumerate(lane):
            start = int(event['tick'])
            end = int(lane[min(index+1, len(lane)-1)]['tick'])
            record = dict(name=' / '.join(event['path']), cat='debugclock', pid=pid, tid=tid,
                          ts=(start-origin)*rate, args=dict(event, level=0, leaf=True,
                          end_tick=str(end), duration_cycle=str(end-start), last_sequence=event['sequence']))
            record.update(dict(ph='X', dur=(end-start)*rate) if end > start else dict(ph='i', s='t'))
            emit(record)
