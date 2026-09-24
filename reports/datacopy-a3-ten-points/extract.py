"""从两个已验证的原始run提取计数与10点数据；输入路径不会写入公开产物。"""
import csv
import hashlib
import json
from pathlib import Path
import sys
import numpy as np
from akl.trace import decode, duration_ticks

raw_root, output = Path(sys.argv[1]), Path(__file__).resolve().parent
sizes = [32, 64, 128, 256, 512, 1024, 4096, 8192, 14336, 32768]
records, runs, launch_total = [], [], 0
for run in ('matrix-01', 'followup-01'):
    folder = raw_root / run
    raw = (folder/'manifest.json').read_bytes()
    m = json.loads(raw)
    assert m['status'] == 'validated'
    for label in ('before', 'after'):
        assert m[f'occupancy_{label}']['observed_idle'] is True
        assert hashlib.sha256((folder/f'occupancy-{label}.txt').read_bytes()).hexdigest() == m[f'occupancy_{label}']['sha256']
    assert all(r['status'] == 'validated' for r in m['cases'])
    launches = sum(r['launches'] for r in m['cases'])
    launch_total += launches
    records += m['cases']
    runs.append(dict(run=run, cases=len(m['cases']), launches=launches,
                     manifest_sha256=hashlib.sha256(raw).hexdigest(), library_sha256=m['build']['library_sha256']))
payload = [r['case'] for r in records if r['case']['control'] == 'payload']
shapes = sorted({(c['block_bytes'], c['blocks'], c['gm_gap_bytes']) for c in payload})
info = dict(soc='Ascend910_9382', cann='9.1.0-beta.1', npu_arch='dav-2201', clock_hz=50000000,
            configurations=len(records), payload_configurations=len(payload), controls=len(records)-len(payload),
            launches=launch_total, shape_fields=['block_bytes', 'blocks', 'gm_gap_bytes'],
            shape_count=len(shapes), shapes=shapes, runs=runs, selected_bytes=sizes,
            conditions=dict(api='DataCopy_params', dtype='uint32', aiv_count=1, loops=128, batch=1, slots=1,
                            memory_scope='local_GM', cache='small reused working set; coldness unverified'),
            note='existing NPU samples, not a new device run; includes loop/address/sync overhead; no subtraction')
(output/'coverage.json').write_text(json.dumps(info, ensure_ascii=False, indent=2)+'\n')
folder = raw_root/'matrix-01'
m = json.loads((folder/'manifest.json').read_text())
summary = {r['name']:r for r in json.loads((folder/'summary.json').read_text())}
rows = []
for direction in ('GM_UB', 'UB_GM'):
    for size in sizes:
        name = f'{direction}_DataCopy_params_{size}_b1'
        case = next(r['case'] for r in m['cases'] if r['case']['name']==name)
        assert case == dict(name=name, direction=direction, api='DataCopy_params', dtype='uint32',
            block_bytes=size, blocks=1, gm_gap_bytes=0, gm_offset_bytes=0, ub_offset_bytes=0,
            loops=128, batch=1, slots=1, control='payload')
        samples = json.loads((folder/name/'samples.json').read_text())
        assert len(samples)==43 and all(s['correctness'] for s in samples)
        ticks = []
        for s in samples:
            if s['trace'] and not s['warmup']:
                events = decode(np.load(folder/name/f"trace-{s['launch']}.npy", allow_pickle=False),
                                m['run_id'], s['launch'], m['device'], [size])
                value = duration_ticks(events, 0)
                assert str(value)==s['ticks'] and value>0
                ticks.append(value)
        assert len(ticks)==20
        values=np.asarray(ticks)*1e6/info['clock_hz']/128
        p50,p95=(float(np.percentile(values,p)) for p in (50,95))
        assert abs(p50-summary[name]['p50_us_per_call'])<1e-12
        assert abs(p95-summary[name]['p95_us_per_call'])<1e-12
        rows.append(dict(case=name, direction=direction, payload_bytes=size, samples=20,
                         p50_us=p50, p95_us=p95, payload_GBps=size/p50/1000,
                         raw_total_ticks=json.dumps(ticks,separators=(',',':'))))
with (output/'points.csv').open('w') as f:
    writer=csv.DictWriter(f, fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
print(f'Validated {len(rows)} points / 400 raw timing samples; {len(shapes)} shapes / {len(records)} configurations / {launch_total} launches.')
