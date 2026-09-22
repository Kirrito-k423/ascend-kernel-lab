"""并行报告、数据等价与成功后清理；CPU 合成数据，不是 NPU 实测。"""
import contextlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote
import zipfile
from akl.batch import export_batch
from akl.semantic import MAGIC, event_map, capture_signature, clean_capture

class Compact(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.source = self.root/'kernel.cpp'
        self.source.write_text('DebugClock("wait"); DebugClock("copy");')
        self.mapping = event_map([self.source]); self.keys = list(self.mapping)

    def capture(self, rank=0, launch=0, parent='', pid=None):
        folder = self.root/parent/f'rank{rank}-pid{pid or rank+100}-launch{launch}'
        folder.mkdir(parents=True)
        meta = dict(schema='akl.semantic.v1', alignment='unverified', capacity=4, blocks=2, rank=rank, device=rank%8)
        (folder/'capture.json').write_text(json.dumps(meta))
        words = []
        for block in range(2):
            words += [MAGIC,1,4,0,block,0,0,1]
            for seq in range(4): words += [self.keys[seq%2],2**60+100*block+10*seq+launch]
        (folder/'trace.bin').write_bytes(struct.pack('<'+str(len(words))+'Q',*words))
        return folder

    def export(self, name='result', **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return export_batch(self.root, self.root/name, self.mapping, [self.source], **kwargs)

    def test_parallel_equivalence_and_cleanup(self):
        inputs = [self.capture(rank, launch) for rank in range(4) for launch in range(2)]
        (inputs[0]/'notes.txt').write_text('keep user file')
        for folder in inputs:
            for name in ('semantic.html','semantic.svg','trace.json','semantic.jsonl','counts.json'):
                (folder/name).write_text('old derived output')
        self.assertEqual(self.export('serial', jobs=1, keep_intermediates=True), 0)
        self.assertEqual(self.export(jobs=4), 0)
        for launch in range(2):
            relative = Path('launches')/f'launch{launch}'
            for name in ('trace.json','summary.json','index.html'):
                self.assertEqual((self.root/'serial'/relative/name).read_bytes(), (self.root/'result'/relative/name).read_bytes())
            trace = json.loads((self.root/'result'/relative/'trace.json').read_text())['traceEvents']
            phases = [e for e in trace if e.get('cat')=='debugclock']
            self.assertEqual(len(phases), 32); self.assertEqual(len({e['pid'] for e in phases}), 4)
            self.assertTrue(all(int(e['args']['tick'])>=2**60 for e in phases))
            report = json.loads((self.root/'result'/relative/'summary.json').read_text())
            for run in report['runs']:
                destination = self.root/'result'/relative/unquote(run['html'])
                self.assertEqual({p.name for p in destination.parent.iterdir()}, {'semantic.html','semantic.svg','trace.json'})
                self.assertEqual(destination.read_bytes(), (self.root/'serial'/relative/unquote(run['html'])).read_bytes())
        self.assertFalse(list((self.root/'result').rglob('*.zip')))
        self.assertFalse(list((self.root/'result').rglob('*.jsonl')))
        self.assertEqual((inputs[0]/'notes.txt').read_text(), 'keep user file')
        self.assertFalse((inputs[0]/'trace.bin').exists()); self.assertTrue(all(not p.exists() for p in inputs[1:]))

    def test_partial_failure_retains_all_raw_and_can_retry(self):
        a, b = self.capture(), self.capture(1)
        original = (b/'trace.bin').read_bytes(); (b/'trace.bin').write_bytes(b'bad')
        self.assertEqual(self.export(jobs=2), 1)
        self.assertTrue((a/'trace.bin').is_file()); self.assertTrue((b/'trace.bin').is_file())
        report = json.loads((self.root/'result/launches/launch0/summary.json').read_text())
        self.assertEqual([r['status'] for r in report['runs']], ['ok','error'])
        (b/'trace.bin').write_bytes(original); self.assertEqual(self.export(jobs=2), 0)
        self.assertFalse(a.exists()); self.assertFalse(b.exists())

    def test_publication_failure_and_changed_capture_do_not_delete_input(self):
        folder = self.capture(); self.export(jobs=1, keep_intermediates=True)
        previous = (self.root/'result/summary.json').read_bytes(); rename = Path.rename
        def fail(path, destination):
            if path.parent.name.startswith('.akl-batch-') and path.name=='result': raise OSError('publish failed')
            return rename(path, destination)
        with patch.object(Path, 'rename', fail), self.assertRaisesRegex(OSError, 'publish failed'):
            self.export(jobs=1)
        self.assertEqual((self.root/'result/summary.json').read_bytes(), previous)
        self.assertTrue((folder/'trace.bin').is_file())
        signature = capture_signature(folder); (folder/'trace.bin').write_bytes(b'new')
        with self.assertRaisesRegex(ValueError, '采集已变化'): clean_capture(folder, signature, True)
        self.assertTrue((folder/'capture.json').is_file())

    def test_optional_zip_is_per_launch_and_experiments_isolated(self):
        self.capture(parent='<A> & space', pid=1); self.capture(parent='<A> & space', pid=2)
        self.capture(parent='B'); self.export(jobs=2, keep_intermediates=True, zip_launches=True)
        output = self.root/'result'; self.assertFalse((output/'result.zip').exists())
        index = json.loads((output/'summary.json').read_text()); self.assertEqual(len(index['launches']), 2)
        self.assertTrue(index['launches'][0]['warning']); self.assertIn('&lt;A&gt;', (output/'index.html').read_text())
        for group in index['launches']:
            directory = output/unquote(group['directory'])
            with zipfile.ZipFile(directory/'result.zip') as z:
                self.assertIsNone(z.testzip()); self.assertIn('result/index.html', z.namelist())
        with self.assertRaises(ValueError): self.export('..', jobs=1)
        with self.assertRaises(ValueError): self.export('bad', jobs=0)

    def test_single_capture_cli_cleanup(self):
        folder = self.capture()
        subprocess.run([sys.executable,'-m','akl.semantic',str(folder),'--source',str(self.source)],check=True,stdout=subprocess.DEVNULL)
        self.assertEqual({p.name for p in folder.iterdir()}, {'semantic.html','semantic.svg','trace.json'})

    def test_last_launch_skips_old_decode_and_cleans_after_success(self):
        for rank in range(2):
            for launch in range(3): self.capture(rank, launch)
        old = self.root/'rank0-pid100-launch0'
        (old/'trace.bin').write_bytes(b'old data need not be decoded')
        self.assertEqual(self.export(jobs=2, last_launch=True), 0)
        report = json.loads((self.root/'result/summary.json').read_text())
        self.assertEqual([r['launch'] for r in report['launches']], [2])
        self.assertEqual(report['skipped_captures'], 4)
        self.assertFalse(list(self.root.glob('rank*')))

    def test_incomplete_last_launch_retains_raw(self):
        a = self.capture(0,0); self.capture(1,0); self.capture(0,1)
        with self.assertRaisesRegex(ValueError, '缺少部分 rank'):
            self.export(jobs=2, last_launch=True)
        self.assertTrue((a/'trace.bin').is_file()); self.assertFalse((self.root/'result').exists())

if __name__=='__main__': unittest.main()
