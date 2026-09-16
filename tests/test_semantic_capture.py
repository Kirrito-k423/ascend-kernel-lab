"""同步导出缩短空白槽位后，旧版 v1 解码仍保留全部原始信息。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'python'))
from akl.semantic import decode_capture


class CaptureExport(unittest.TestCase):
    def test_compaction_errors_and_lifetime(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/'captures'
            executable = Path(tmp)/'capture-test'
            subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-pthread',
                '-I'+str(ROOT/'tests/cpu_stubs'), '-I'+str(ROOT/'include'),
                str(ROOT/'tests/semantic_capture.cpp'), '-o', str(executable)], check=True)
            subprocess.run([str(executable), str(output)], check=True)
            for name, capacity, count in [('mixed', 22, 25), ('full', 256, 273),
                                          ('thread-a', 4, 4), ('thread-b', 22, 29)]:
                for folder in (output/name).iterdir():
                    meta, events, warnings = decode_capture(folder, {7: ['loop']})
                    self.assertEqual((meta['capacity'], meta['recorder_capacity']), (capacity, 256))
                    self.assertEqual(len(events), count)
                    self.assertTrue(warnings)
                    self.assertGreaterEqual(min(int(e['tick']) for e in events), 2**60)
            for name, capacity in [('empty', 2), ('damaged', 256)]:
                for folder in (output/name).iterdir():
                    self.assertEqual(json.loads((folder/'capture.json').read_text())['capacity'], capacity)
                    with self.assertRaises(ValueError):
                        decode_capture(folder, {7: ['loop']})
