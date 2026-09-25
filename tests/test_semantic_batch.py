"""批量报告使用 CPU 合成数据，不代表 NPU 实测。"""
import contextlib
import io
import json
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from akl.batch import export_batch
from akl.semantic import MAGIC, event_map


class Batch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'kernel.cpp'
        self.source.write_text('DebugClock("loop", "iteration");')
        self.mapping = event_map([self.source])
        self.key = next(iter(self.mapping))
        self.output = self.root/'result'

    def capture(self, relative, rank=0, dropped=0):
        folder = self.root/relative
        folder.mkdir(parents=True)
        (folder/'capture.json').write_text(json.dumps(dict(schema='akl.semantic.v1',
            alignment='unverified', capacity=2, blocks=1, rank=rank, device=0)))
        (folder/'trace.bin').write_bytes(struct.pack('<12Q', MAGIC, 1, 2, dropped,
            0, 0, 0, 1, self.key, 2**60, self.key, 2**60+7))
        return folder

    def export(self, output=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return export_batch(self.root, output or self.output, self.mapping, [self.source])

    def test_nested_runs_portable_archive_and_exact_counts(self):
        for experiment in ('first', 'second'):
            for rank in range(2):
                self.capture(f'{experiment}/rank{rank}-pid100-launch0', rank)
        self.assertEqual(self.export(), 0)
        report = json.loads((self.output/'summary.json').read_text())
        self.assertEqual(len(report['runs']), 4)
        self.assertEqual([s['count'] for s in report['stats']], ['4', '4'])
        self.assertEqual([s['sum_cycle'] for s in report['stats']], ['14', '14'])
        events = [json.loads(line) for line in (self.output/'events.jsonl').read_text().splitlines()]
        self.assertEqual(len(events), 8)
        self.assertEqual(len({e['capture_id'] for e in events}), 4)
        self.assertEqual(events[0]['tick'], str(2**60))
        with zipfile.ZipFile(self.output/'result.zip') as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn('result/index.html', archive.namelist())
            for run in report['runs']:
                self.assertIn('result/'+run['html'], archive.namelist())
                self.assertIn('result/runs/'+run['id']+'/trace.bin', archive.namelist())
        self.assertEqual(self.export(), 0)
        self.assertEqual(self.export(self.root/'another-report'), 0)
        self.assertEqual(len(json.loads((self.root/'another-report/summary.json').read_text())['runs']), 4)

    def test_failed_and_dropped_captures_remain_visible(self):
        good = self.capture('rank0-pid1-launch0', dropped=3)
        bad = self.capture('rank1-pid2-launch0', rank=1)
        (bad/'trace.bin').write_bytes(b'truncated')
        self.assertEqual(self.export(), 1)
        report = json.loads((self.output/'summary.json').read_text())
        self.assertEqual([r['status'] for r in report['runs']], ['ok', 'error'])
        self.assertIn('dropped=3', report['runs'][0]['warnings'][0])
        self.assertTrue((good/'semantic.html').exists())
        self.assertEqual((self.output/'runs'/bad.name/'trace.bin').read_bytes(), b'truncated')
        self.assertEqual(report['stats'][0]['count'], '2')
        (bad/'capture.json').unlink()
        self.assertEqual(self.export(), 1)
        self.assertFalse((self.output/'runs'/bad.name/'capture.json').exists())

    def test_output_guards_and_hostile_names(self):
        with self.assertRaises(ValueError):
            self.export()
        folder = self.capture('experiment </script>/rank0-pid1-launch0')
        self.output.mkdir()
        (self.output/'keep.txt').write_text('unrelated')
        with self.assertRaises(ValueError):
            self.export()
        self.assertEqual((self.output/'keep.txt').read_text(), 'unrelated')
        with self.assertRaises(ValueError):
            self.export(folder/'result')
        with self.assertRaises(ValueError):
            self.export(self.root)
        self.assertEqual(self.export(self.root/'safe'), 0)
        page = (self.root/'safe/index.html').read_text()
        self.assertNotIn('experiment </script>', page)
        self.assertIn('\\u003c', page)


if __name__ == '__main__':
    unittest.main()
