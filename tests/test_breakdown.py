"""合成 ABI 回放验证统计口径、PNG/HTML、并行批处理及清理；不是 NPU 实测。"""
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from akl.breakdown import REPORTS, aggregate
from akl.semantic import MAGIC, decode_capture, path_hash, render


def capture(folder, blocks=4, rank=0, dropped=0):
    folder.mkdir(parents=True, exist_ok=True)
    paths = [('dispatch', 'send'), ('dispatch', 'wait'), ('dispatch', 'copy'), ('end',)]
    mapping = {path_hash(p): list(p) for p in paths}
    meta = dict(schema='akl.semantic.v1', capacity=64, blocks=blocks,
                rank=rank, device=rank, alignment='unverified')
    (folder/'capture.json').write_text(json.dumps(meta))
    rows = []
    for block in range(blocks):
        tick = 2**60 + block * 1000
        row = [MAGIC, 1, 49, dropped, block, block % 2, 0, 1]
        for seq in range(49):
            row += [path_hash(paths[seq % 3] if seq < 48 else paths[-1]), tick]
            tick += 10 + (seq % 3 + 1) * (block + 1)
        rows += row + [0] * (136 - len(row))
    (folder/'trace.bin').write_bytes(struct.pack(f'<{len(rows)}Q', *rows))
    return mapping


class Breakdown(unittest.TestCase):
    def test_loops_missing_cores_and_ratio_of_means(self):
        events = []
        for block, lane in enumerate(([('A', 0), ('B', 10), ('A', 30), ('end', 60)],
                                      [('A', 0), ('end', 100)], [('A', 0)],
                                      [('A', 0), ('end', 0)])):
            for name, offset in lane:
                events.append(dict(block=block, subblock=0, path=[name], tick=str(2**60+offset)))
        paths, panels = aggregate({'blocks': 5}, events)
        self.assertEqual(paths, [('A',), ('B',), ('end',)])
        self.assertEqual(panels[1]['totals'], [40, 20, 0])
        self.assertEqual(panels[0]['totals'], [140, 20, 0])
        self.assertEqual(panels[0]['divisor'], 3)
        self.assertAlmostEqual(panels[0]['totals'][0]/sum(panels[0]['totals']), .875)
        self.assertFalse(panels[3]['valid'])
        self.assertFalse(panels[5]['valid'])
        self.assertTrue(panels[4]['valid'])
        # subblock 是独立通道，不能把前一通道的尾点接到后一通道。
        events += [dict(block=0, subblock=1, path=['B'], tick=str(2**60+i)) for i in (0, 7)]
        self.assertEqual(aggregate({'blocks': 5}, events)[1][2]['totals'], [0, 7, 0])

    def test_single_report_and_failure_retains_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            mapping = capture(folder, dropped=2)
            meta, events, warnings = decode_capture(folder, mapping)
            events[0]['path'] = ['</script><script>bad()</script>']
            render(folder, meta, events, warnings, clock_mhz=50, cycle_range=(0, 1))
            text = (folder/'semantic.html').read_text()
            self.assertIn('dropped=2', text)
            self.assertNotIn('<script>bad()', text)
            payload = json.loads(re.search(r'id="breakdown-data">(.*?)</script>', text).group(1))
            self.assertEqual(payload['factor'], .02)
            self.assertEqual(len(payload['panels']), 5)
            # 缩放范围不能截断耗时统计。
            self.assertGreater(sum(map(int, payload['panels'][1]['totals'])), 1)
            for name in REPORTS:
                self.assertEqual((folder/name).read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
            with patch('akl.semantic.render_breakdown', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    render(folder, meta, events, warnings)
            self.assertTrue((folder/'trace.bin').exists())

    def test_no_intervals_and_zero_span_render_as_na(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            mapping = capture(folder)
            meta, events, _ = decode_capture(folder, mapping)
            events = [e for e in events if e['block'] == 0][:2]
            events[1]['tick'] = events[0]['tick']
            for retained in (events[:1], events):
                render(folder, meta, retained, [])
                text = (folder/'semantic.html').read_text()
                data = json.loads(re.search(r'id="breakdown-data">(.*?)</script>', text).group(1))
                self.assertEqual(data['panels'][0]['valid'], len(retained) == 2)
                self.assertEqual(sum(map(int, data['panels'][0]['totals'])), 0)

    def test_parallel_batch_portable_pngs_and_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rank in range(2):
                capture(root/f'rank{rank}-pid1-launch0', rank=rank)
            source = root/'kernel.cpp'
            source.write_text('DebugClock("dispatch", "send"); DebugClock("dispatch", "wait");'
                              'DebugClock("dispatch", "copy"); DebugClock("end");')
            subprocess.run([sys.executable, '-m', 'akl.semantic', str(root), '--source', str(source),
                            '--jobs', '2'], check=True, capture_output=True)
            self.assertFalse(list(root.glob('rank*')))
            group = root/'result/launches/launch0'
            self.assertIn('单次 core 耗时占比', (group/'index.html').read_text())
            for rank in range(2):
                result = group/f'runs/rank{rank}-pid1-launch0'
                self.assertEqual({p.name for p in result.iterdir()},
                                 {'semantic.html', 'semantic.svg', 'trace.json', *REPORTS})
                self.assertIn('breakdown_duration.png', (result/'semantic.html').read_text())
            self.assertFalse(list(root.rglob('*.zip')))


if __name__ == '__main__':
    unittest.main()
