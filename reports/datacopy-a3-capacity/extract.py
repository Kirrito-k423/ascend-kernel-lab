"""校验本轮全部原始run，导出脱敏参数/原始tick；不复制机器日志。"""
import csv
import hashlib
import json
from pathlib import Path
import sys
from akl.datacopy_report import analyse

raw = Path(sys.argv[1])
out = Path(__file__).resolve().parent
groups = {
    'sweep-small': ('small', 1, 128), 'sweep-ring': ('ring64', 1, 128),
    'long-window': ('small-long', 1, 8192),
    'w2-small-long': ('small', 2, 8192), 'w2-ring-long': ('ring64', 2, 8192),
}
points, records = [], []
for folder in sorted(raw.iterdir()):
    if not (folder / 'manifest.json').is_file(): continue
    m = json.loads((folder / 'manifest.json').read_text())
    rows = analyse(folder, render_figures=False)
    prefix = folder.name.rsplit('-', 1)[0]
    performance = prefix in groups
    records.append(dict(run=folder.name, kind='performance' if performance else 'correctness_smoke',
        status=m['status'], cases=len(rows), launches=sum(c['launches'] for c in m['cases']),
        manifest_sha256=hashlib.sha256((folder / 'manifest.json').read_bytes()).hexdigest(),
        library_sha256=m['build']['library_sha256'], kernel_sha256=m['build']['source_sha256']['kernels/datacopy.cpp']))
    if not performance: continue
    mode, windows, min_loops = groups[prefix]
    configs = {c['case']['name']: c['case'] for c in m['cases']}
    for r in rows:
        name = r['name']
        samples = json.loads((folder / name / 'samples.json').read_text())
        ticks = [int(s['ticks']) for s in samples if s['trace'] and not s['warmup']]
        points.append(dict(run=folder.name, repeat=int(folder.name[-1]), mode=mode, windows=windows,
            direction=r['direction'], bytes=r['block_bytes'], batch=r['batch'], loops=r['loops'],
            working_set_bytes=r['working_set_bytes'], samples=len(ticks),
            p50_us=r['p50_us_per_call'], p95_us=r['p95_us_per_call'], GBps=r['payload_GBps_at_p50'],
            case_json=json.dumps(configs[name], separators=(',', ':')), raw_total_ticks=json.dumps(ticks)))
assert len(records) == 13 and len(points) == 956
assert all(r['status'] == 'validated' for r in records)
with (out / 'points.csv').open('w') as f:
    writer = csv.DictWriter(f, fieldnames=list(points[0])); writer.writeheader(); writer.writerows(points)
coverage = dict(soc='Ascend910_9382', cann='9.1.0-beta.1', npu_arch='dav-2201', clock_hz=50000000,
    ub_bytes=196608, used_aiv_count=1, dtype='uint32 for performance; five dtypes in smoke only',
    unique_performance_configs=len(points)//2, timing_samples=sum(p['samples'] for p in points),
    total_launches=sum(r['launches'] for r in records), runs=records)
(out / 'coverage.json').write_text(json.dumps(coverage, ensure_ascii=False, indent=2))
print(f"Validated {coverage['total_launches']} launches; exported {len(points)} rows / {coverage['timing_samples']} raw timing samples.")
