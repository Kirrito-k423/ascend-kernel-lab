"""CPU 合成采集：单行阶段与按 launch 隔离，不代表 NPU 实测。"""
import contextlib
import io
import json
import re
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.parse import unquote
from akl.batch import export_batch
from akl.semantic import MAGIC, event_map, decode_capture, render

class Basic(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.out=self.root/'result';self.source=self.root/'kernel.cpp'
        self.source.write_text('DebugClock("Init(buffer/tiling)"); DebugClock("LW:wait"); DebugClock("LW:copy"); DebugClock("Process:done");')
        self.mapping=event_map([self.source]);self.keys=list(self.mapping)

    def capture(self,rank,launch,parent='',pid=None):
        folder=self.root/parent/f'rank{rank}-pid{pid or 1000+rank}-launch{launch}';folder.mkdir(parents=True)
        meta=dict(schema='akl.semantic.v1',alignment='unverified',capacity=8,blocks=2,rank=rank,device=rank%8)
        (folder/'capture.json').write_text(json.dumps(meta))
        words=[]
        for block in range(2):
            row=[MAGIC,1,8,0,block,0,0,1]
            for seq,k in enumerate([0,1,2,1,2,1,2,3]):row += [self.keys[k],2**60+launch*10000+block*100+seq*10]
            words+=row
        (folder/'trace.bin').write_bytes(struct.pack('<'+str(len(words))+'Q',*words));return folder

    def export(self,out=None):
        with contextlib.redirect_stdout(io.StringIO()):return export_batch(self.root,out or self.out,self.mapping,[self.source])

    def test_flat_intervals_and_repeated_phases(self):
        folder=self.capture(0,0);meta,events,warnings=decode_capture(folder,self.mapping)
        self.assertEqual([e['occurrence'] for e in events[:8]],[1,1,1,2,2,3,3,1])
        # 即使读入旧多级字符串，展示也只生成一层，不额外复制父区间。
        events[0]['path']=['dispatch','Init'];render(folder,meta,events,warnings)
        trace=json.loads((folder/'trace.json').read_text())['traceEvents']
        phases=[e for e in trace if e.get('cat')=='debugclock']
        self.assertEqual(len(phases),16);self.assertTrue(all(e['args']['level']==0 for e in phases))
        self.assertEqual(phases[0]['name'],'dispatch / Init');self.assertEqual(phases[0]['dur'],.01)
        self.assertTrue(all('work' not in e['args'] for e in phases))
        svg=(folder/'semantic.svg').read_text();self.assertEqual(svg.count('data-level="0"'),16)
        self.assertNotIn('data-level="1"',svg);self.assertIn(str(2**60),svg)

    def test_twenty_launches_and_sixty_four_ranks(self):
        for launch in range(20):
            for rank in range(64):self.capture(rank,launch)
        self.assertEqual(self.export(),0)
        index=json.loads((self.out/'summary.json').read_text());self.assertEqual(len(index['launches']),20)
        self.assertFalse((self.out/'trace.json').exists());self.assertFalse((self.out/'events.jsonl').exists())
        sizes=[]
        for group in index['launches']:
            folder=self.out/unquote(group['directory']);report=json.loads((folder/'summary.json').read_text())
            self.assertEqual(len(report['runs']),64);self.assertEqual({r['launch'] for r in report['runs']},{group['launch']})
            phases=[e for e in json.loads((folder/'trace.json').read_text())['traceEvents'] if e.get('cat')=='debugclock']
            self.assertEqual(len(phases),1024);self.assertTrue(all(e['args']['level']==0 for e in phases))
            sizes.append((folder/'trace.json').stat().st_size)
            with zipfile.ZipFile(folder/'result.zip') as archive:
                self.assertIsNone(archive.testzip());self.assertIn('result/index.html',archive.namelist())
                for run in report['runs']:self.assertIn('result/'+unquote(run['html']),archive.namelist())
        self.assertLess((self.out/'index.html').stat().st_size,20000)
        with zipfile.ZipFile(self.out/'result.zip') as archive:self.assertIsNone(archive.testzip())
        print(f'PASS: 20 launch reports x 64 ranks; 1024 flat events each; index={(self.out/"index.html").stat().st_size} bytes; max trace={max(sizes)} bytes')

    def test_experiment_isolation_failures_and_reruns(self):
        a=self.capture(0,0,'exp A');b=self.capture(1,0,'exp A');self.capture(0,0,'exp B')
        (b/'trace.bin').write_bytes(b'truncated');self.assertEqual(self.export(),1)
        report=json.loads((self.out/'summary.json').read_text());self.assertEqual(len(report['launches']),2)
        first=self.out/unquote(report['launches'][0]['directory']);self.assertEqual(report['launches'][0]['failed'],1)
        errors=[r for r in json.loads((first/'summary.json').read_text())['runs'] if r['status']=='error']
        self.assertEqual(len(errors),1);self.assertTrue((a/'semantic.html').is_file())
        self.assertEqual(self.export(),1);self.assertEqual(self.export(self.root/'another'),1)
        self.assertEqual(len(json.loads((self.root/'another/summary.json').read_text())['launches']),2)
        with self.assertRaises(ValueError):self.export(self.root)
        (self.root/'keep').mkdir();(self.root/'keep/file').write_text('untouched')
        with self.assertRaises(ValueError):self.export(self.root/'keep')
        self.assertEqual((self.root/'keep/file').read_text(),'untouched')

    def test_escaped_names_and_duplicate_rank_warning(self):
        self.capture(0,0,'<script> & spaces',1);self.capture(0,0,'<script> & spaces',2)
        self.assertEqual(self.export(),0);page=(self.out/'index.html').read_text()
        self.assertNotIn('<script>',page);self.assertIn('&lt;script&gt;',page)
        group=json.loads((self.out/'summary.json').read_text())['launches'][0]
        self.assertTrue(group['warning']);self.assertEqual(group['captures'],2)
        self.assertTrue((self.out/unquote(group['directory'])/'index.html').is_file())

if __name__=='__main__':unittest.main()
