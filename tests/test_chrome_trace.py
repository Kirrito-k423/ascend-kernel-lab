"""Chrome Trace 回归使用 CPU 合成采集；不代表 NPU 实测。"""
import contextlib
import io
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from akl.batch import export_batch
from akl.chrome_trace import trace_file, write_capture
from akl.semantic import MAGIC, decode_capture, path_hash, render


class ChromeTrace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paths = [['big', 'sub', 'a'], ['big', 'sub', 'a'], ['big', 'b'], ['end']]
        self.mapping = {path_hash(path): path for path in self.paths}
        self.meta = dict(schema='akl.semantic.v1', alignment='unverified', blocks=2,
                         capacity=4, rank=0, device=3)

    def capture(self, relative):
        folder = self.root/relative
        folder.mkdir(parents=True)
        (folder/'capture.json').write_text(json.dumps(self.meta))
        rows = []
        for block in range(2):
            row = [MAGIC, 1, 4, 2, block, block+1, 0, 1]
            for path, tick in zip(self.paths, [0, 0, 7, 10]):
                row.extend([path_hash(path), 2**60 + block*3 + tick])
            rows.extend(row)
        (folder/'trace.bin').write_bytes(struct.pack('<32Q', *rows))
        return folder

    def test_intervals_raw_ticks_hierarchy_and_units(self):
        folder = self.capture('rank0-pid1-launch0')
        meta, events, warnings = decode_capture(folder, self.mapping)
        for mhz, rate in [(None, .001), (500, .002)]:
            render(folder, meta, events, warnings, mhz, (0, 1))
            trace = json.loads((folder/'trace.json').read_text())['traceEvents']
            leaves = [event for event in trace if event.get('args', {}).get('leaf')]
            self.assertEqual(len(leaves), len(events))  # 窗口不裁剪 trace，重复/零长度/末点不丢失。
            self.assertEqual({event['args']['tick'] for event in leaves}, {e['tick'] for e in events})
            self.assertEqual(sorted(e['args']['occurrence'] for e in leaves[:2]), [1, 2])
            self.assertEqual([e['dur'] for e in leaves if e['ph']=='X'], [7*rate, 3*rate]*2)
            self.assertEqual(sum(e['ph']=='i' for e in leaves), 4)
            self.assertEqual([e['ts'] for e in leaves if e['tid']==2][0], 3*rate)
            info = next(e['args'] for e in trace if e['name']=='capture (independent clock)')
            self.assertEqual(info['origin_cycle'], str(2**60))
            self.assertEqual(info['cycle_us'], rate)
            self.assertIn('dropped=2', info['warnings'][0])
            for tid in (1, 2):
                stack = []
                for e in [e for e in trace if e.get('tid')==tid and e['ph']=='X']:
                    lo, hi = int(e['args']['tick']), int(e['args']['end_tick'])
                    while stack and lo >= stack[-1]:
                        stack.pop()
                    self.assertTrue(not stack or hi <= stack[-1])
                    stack.append(hi)
            self.assertIn('href="trace.json"', (folder/'semantic.html').read_text())

    def test_batch_unique_captures_errors_and_portable_json(self):
        for experiment in ('one', 'two'):
            self.capture(experiment+'/rank0-pid1-launch0')
        broken = self.capture('rank0-pid1-launch1')
        (broken/'trace.bin').write_bytes(b'bad')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(export_batch(self.root, self.root/'result', self.mapping, []), 1)
        trace = json.loads((self.root/'result/trace.json').read_text())['traceEvents']
        processes = [e for e in trace if e['ph']=='M' and e['name']=='process_name']
        self.assertEqual(len({e['pid'] for e in processes}), 2)
        self.assertEqual(len({e['args']['name'] for e in processes}), 2)
        self.assertEqual(sum(e.get('args', {}).get('leaf', False) for e in trace), 16)
        self.assertEqual(sum(e['name']=='capture error' for e in trace), 1)
        with zipfile.ZipFile(self.root/'result/result.zip') as archive:
            self.assertEqual(json.loads(archive.read('result/trace.json'))['traceEvents'], trace)
            for experiment in ('one', 'two'):
                relative = experiment+'/rank0-pid1-launch0/trace.json'
                self.assertEqual(archive.read('result/runs/'+relative), (self.root/relative).read_bytes())

    def test_subblocks_have_distinct_threads(self):
        base = dict(block=0, sequence=0, event_id=1, occurrence=1, tick=str(2**64-1), path=['last'])
        events = [dict(base, subblock=subblock) for subblock in (0, 1)]
        with trace_file(self.root/'trace.json') as emit:
            write_capture(emit, 'same block', 1, self.meta, events, [])
        trace = json.loads((self.root/'trace.json').read_text())['traceEvents']
        leaves = [e for e in trace if e.get('args', {}).get('leaf')]
        self.assertEqual({e['tid'] for e in leaves}, {1, 2})
        self.assertTrue(all(e['args']['tick']==str(2**64-1) and e['ts']==0 for e in leaves))


if __name__ == '__main__':
    unittest.main()
