"""容量/平台规则的 CPU 契约测试，不产生 NPU 性能数据。"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
import numpy as np
from akl.datacopy import CopyCase, make_buffers, suite
from akl.datacopy_sweep import plan, plateau, report


class SweepContracts(unittest.TestCase):
    def test_large_copy_field_units_and_oracle(self):
        for api in ('DataCopy_params', 'DataCopy_count'):
            for direction in ('GM_UB', 'UB_GM'):
                c = CopyCase('large', api=api, direction=direction, block_bytes=196288, loops=1)
                self.assertEqual(c.params()['ub_stride_bytes'] + 320, 196608)
                x, _, expected, mask = make_buffers(c)
                np.testing.assert_array_equal(x, expected[:len(x)])
                self.assertTrue(np.all(expected[-128:] == 0xa5))
                self.assertTrue(mask.all())
        with self.assertRaises(ValueError):
            replace(c, api='DataCopyPad_params').params()
        with self.assertRaises(ValueError):
            replace(c, block_bytes=65536 * 32).params()

    def test_batch_can_use_more_than_old_128k_budget(self):
        c = CopyCase('larger', block_bytes=32768, batch=4, slots=4)
        self.assertEqual(c.params()['ub_stride_bytes'] * c.batch, 131072)
        self.assertEqual(len(suite()), 148)  # 保留旧默认矩阵。

    def test_scan_reaches_capacity_and_never_shrinks_ring(self):
        for mode in ('small', 'ring'):
            spec = plan(196608, mode)
            self.assertEqual(len(spec['cases']), len({c['name'] for c in spec['cases']}))
            self.assertEqual(max(c['block_bytes'] for c in spec['cases']), 196288)
            for raw in spec['cases']:
                c = CopyCase(**raw)
                layout = c.layout()
                self.assertLessEqual(layout['ub_working_set_bytes'] + 320, 196608)
                self.assertGreaterEqual(c.loops * c.batch, 2 * c.slots)
                if mode == 'ring':
                    self.assertGreaterEqual(layout['gm_working_set_bytes'], 64 * 1024**2)
                    self.assertLess(layout['gm_working_set_bytes'], 64 * 1024**2 + c.block_bytes * c.batch)
        self.assertTrue(plan(196608, 'ring')['skipped'])

    def test_plateau_requires_range_repeat_and_low_noise(self):
        def points(values):
            return [dict(bytes=n, repeat_GBps=v, p95_over_p50=[1.02] * len(v))
                    for n, v in zip((65536, 131072, 196288), values)]
        flat = points([[99, 100], [102, 101], [103, 102]])
        self.assertEqual(plateau(flat)['status'], 'candidate_plateau')
        self.assertEqual(plateau(points([[50, 51], [80, 81], [100, 101]]))['status'], 'not_observed')
        self.assertEqual(plateau(points([[100], [100], [100]]))['status'], 'not_observed')
        self.assertEqual(plateau(flat[:2])['status'], 'insufficient_range')
        flat[-1]['p95_over_p50'] = [1.3, 1.3]
        self.assertEqual(plateau(flat)['status'], 'not_observed')

    def test_same_run_not_a_repeat(self):
        with self.assertRaisesRegex(ValueError, '两次独立'):
            report(plan(196608), [Path('same'), Path('./same')], Path('unused'))

    def test_report_checks_raw_evidence_before_rendering(self):
        from akl.cases import MAGIC, WORDS
        from unittest.mock import patch
        spec = plan(352)  # CPU fixture: 只容纳32B，绝不冒充真实设备容量。
        with tempfile.TemporaryDirectory() as tmp, patch('akl.datacopy_report.render'):
            root = Path(tmp)
            runs = []
            for repeat in (1, 2):
                run = root / f'CPU-fixture-only-{repeat}'
                run.mkdir(); runs.append(run)
                m = dict(schema='akl.datacopy.v1', status='validated', run_id=run.name, device=0,
                         profile=dict(soc='CPU-fixture-only', topology='test', memory_scope='local_GM', clock_hz=50000000),
                         hardware=dict(ub_bytes=352), arguments=dict(samples=20), cases=[],
                         build=dict(npu_arch='test', cann_install='test', compiler='test', library_sha256='test'))
                for label in ('before', 'after'):
                    (run / f'occupancy-{label}.txt').write_text('CPU-fixture-only')
                    m[f'occupancy_{label}'] = dict(observed_idle=True, sha256=hashlib.sha256(b'CPU-fixture-only').hexdigest())
                for config in spec['cases']:
                    c = CopyCase(**config)
                    folder = run / c.name
                    folder.mkdir()
                    raw = np.zeros((1, WORDS), dtype=np.uint64)
                    raw[0, :8] = [MAGIC, 1, 5, 0, 0, 0, 32, 1]
                    for i, tick in enumerate((100, 200, 201, 1201, 1300)):
                        raw[0, 8 + 2*i:10 + 2*i] = [i, tick]
                    samples = []
                    for launch in range(40):
                        traced = launch % 2 == 0
                        s = dict(launch=launch, trace=traced, warmup=False, correctness=True)
                        if traced:
                            s['ticks'] = '1000'
                            np.save(folder / f'trace-{launch}.npy', raw)
                        samples.append(s)
                    (folder / 'samples.json').write_text(json.dumps(samples))
                    m['cases'].append(dict(case=config, params=c.params(), layout=c.layout(), status='validated', launches=40, expected_retained=32))
                (run / 'manifest.json').write_text(json.dumps(m))
            result = report(spec, runs, root / 'report')
            self.assertTrue((root / 'report/throughput-sweep.png').is_file())
            self.assertTrue(all(g['plateau']['status'] == 'insufficient_range' for g in result['groups']))
            broken = runs[1] / spec['cases'][0]['name'] / 'trace-0.npy'
            raw = np.load(broken); raw[0, 15] += 1; np.save(broken, raw)
            with self.assertRaisesRegex(ValueError, 'raw tick'):
                report(spec, runs, root / 'bad-report')


if __name__ == '__main__':
    unittest.main()
