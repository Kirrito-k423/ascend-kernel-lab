"""容量/平台规则的 CPU 契约测试，不产生 NPU 性能数据。"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
import numpy as np
from akl.datacopy import CopyCase, make_buffers, suite
from akl.datacopy_sweep import plan, plateau, report


class SweepContracts(unittest.TestCase):
    def test_bundle_preserves_failure_and_never_drops_ticks_for_size(self):
        from akl.datacopy_bundle import bundle
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = root / 'failed-fixture'; run.mkdir()
            (run / 'manifest.json').write_text(json.dumps(dict(schema='akl.datacopy.v1', status='failed', cases=[])))
            (run / 'error.txt').write_text('CPU fixture error')
            output = root / 'result.zip'
            size = bundle([run], output)
            self.assertLess(size, 5_000_000)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read('failed-fixture/error.txt'), b'CPU fixture error')
                self.assertEqual(json.loads(archive.read('bundle.json'))['runs'][0]['status'], 'failed')
            with self.assertRaises(FileExistsError): bundle([run], output)
            with self.assertRaisesRegex(ValueError, '超过回传上限'): bundle([run], root / 'too-small.zip', limit=10)
            self.assertFalse((root / 'too-small.zip').exists())
            self.assertTrue((run / 'manifest.json').exists())

    def test_two_windows_oracle_matches_sequential_simulation(self):
        for direction in ('GM_UB', 'UB_GM'):
            for loops, slots, batch in ((2, 2, 1), (5, 3, 1), (7, 6, 2), (8, 6, 2)):
                c = CopyCase('windows', direction=direction, api='DataCopyPad_params', block_bytes=28,
                             blocks=2, gm_gap_bytes=4, ub_offset_bytes=32, gm_offset_bytes=4,
                             loops=loops, slots=slots, batch=batch, windows=2)
                x, output, expected, defined = make_buffers(c)
                p = c.params()
                ub = np.zeros(c.layout()['ub_working_set_bytes'], dtype=np.uint8) if direction == 'GM_UB' else x.copy()
                gm = x if direction == 'GM_UB' else output
                for group in range(loops):
                    for j in range(batch):
                        for b in range(c.blocks):
                            ui = ((group % 2) * batch + j) * p['ub_stride_bytes'] + 32 + b * 32
                            gi = ((group * batch + j) % slots) * p['gm_stride_bytes'] + 4 + b * 32
                            if direction == 'GM_UB': ub[ui:ui+28] = gm[gi:gi+28]
                            else: gm[gi:gi+28] = ub[ui:ui+28]
                if direction == 'GM_UB': output[:len(ub)] = ub
                np.testing.assert_array_equal(output[defined], expected[defined])
        self.assertNotIn('windows', CopyCase('old').signature())
        self.assertEqual(c.signature()['windows'], 2)
        self.assertEqual(c.params()['reserved1'], 1)
        with self.assertRaises(ValueError): CopyCase('bad', windows=2).params()

    def test_window_scan_capacity_and_long_duration(self):
        spec = plan(196608, windows=2, min_loops=8192)
        self.assertEqual(max(c['block_bytes'] for c in spec['cases']), 98144)
        for raw in spec['cases']:
            c = CopyCase(**raw)
            self.assertEqual(c.windows, 2)
            self.assertGreaterEqual(c.loops, 8192)
            self.assertLessEqual(c.layout()['ub_working_set_bytes'] + 320, 196608)

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
