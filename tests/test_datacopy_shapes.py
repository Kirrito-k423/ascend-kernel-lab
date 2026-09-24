"""真实shape ABI的CPU验证；不代表A5上板通过。"""
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
from akl.datacopy_shapes import decode_shapes,MAGIC,SLOTS,RECORD_WORDS


class ShapeContracts(unittest.TestCase):
    def raw(self):
        words=[0]*(8+SLOTS*RECORD_WORDS)
        words[:8]=[MAGIC,1,SLOTS,0,0,0,RECORD_WORDS,1]
        words[8:28]=[1,1,4,2,0,2,2,1,14336,0,0,9,0,1,1,1,14336,0,0,0]
        meta=dict(schema='akl.datacopy.capture.v1',blocks=1,slots=SLOTS,record_words=RECORD_WORDS)
        return words,meta

    def test_real_units_and_count(self):
        words,meta=self.raw();rows,warnings=decode_shapes(struct.pack(f'<{len(words)}Q',*words),meta)
        r=rows[0];self.assertEqual(r['dtype'],'bfloat16');self.assertEqual(r['group'],0)
        self.assertEqual(r['payload_bytes_per_call'],14336);self.assertEqual(r['total_payload_bytes'],str(9*14336))
        self.assertIsNone(r['expected_us']);self.assertEqual(warnings,[])
        words[8+3]=0;words[8+8]=7936;words[8+16]=15872
        rows,_=decode_shapes(struct.pack(f'<{len(words)}Q',*words),meta)
        self.assertEqual(rows[0]['payload_bytes_per_call'],15872)

    def test_truncation_uncommitted_and_payload_corruption(self):
        for change in ('truncate','commit','payload'):
            words,meta=self.raw()
            if change=='truncate':words.pop()
            elif change=='commit':words[7]=0
            else:words[24]=32
            with self.assertRaises(ValueError):decode_shapes(struct.pack(f'<{len(words)}Q',*words),meta)

    def test_shape_change_and_overflow_are_visible(self):
        words,meta=self.raw();words[3]=3;words[20]=2
        rows,warnings=decode_shapes(struct.pack(f'<{len(words)}Q',*words),meta)
        self.assertFalse(rows[0]['valid_shape']);self.assertEqual(len(warnings),2)

    def test_source_bound_decode_and_candidate(self):
        import hashlib
        from akl.datacopy_shapes import convert
        from akl.semantic import path_hash
        from akl.datacopy import CopyCase
        from akl.cases import MAGIC as TRACE_MAGIC
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);capture=root/'capture';capture.mkdir()
            source=root/'kernel.cpp';source.write_text('DebugClock("TokenCopyToBuffer:group-prepare");')
            sha=hashlib.sha256(source.read_bytes()).hexdigest()
            words,meta=self.raw();words[10]=0
            meta.update(rank=0,soc='synthetic-only',cann_version='synthetic-only',h=7168,k=8,tokens=9,source_sha256=sha)
            (capture/'datacopy-shapes.json').write_text(json.dumps(meta))
            (capture/'datacopy-shapes.bin').write_bytes(struct.pack(f'<{len(words)}Q',*words))
            (capture/'capture.json').write_text(json.dumps(dict(schema='akl.semantic.v1',capacity=2,blocks=1,rank=0,device=0,alignment='unverified')))
            trace=[TRACE_MAGIC,1,1,0,0,0,0,1,path_hash(['TokenCopyToBuffer:group-prepare']),1000,0,0]
            (capture/'trace.bin').write_bytes(struct.pack('<12Q',*trace))
            result=convert(capture,[source],root/'decoded')
            self.assertEqual(result['observations'][0]['anchor_tick'],'1000')
            candidates=json.loads((root/'decoded/candidate-cases.json').read_text())
            self.assertEqual(candidates[0]['dtype'],'bfloat16')
            self.assertEqual(CopyCase(**candidates[0]).params()['reserved0'],1)
            source.write_text('DebugClock("other");')
            with self.assertRaises(ValueError):convert(capture,[source],root/'wrong-source')

    @unittest.skipUnless(shutil.which('c++'),'需要C++编译器验证设备共用的纯计数协议')
    def test_cpp_table_aggregation(self):
        root=Path(__file__).resolve().parents[1]
        code='''#include "akl/datacopy_shape_protocol.h"
#include <cassert>
int main(){
 akl::copy_shape::Table t;
 uint64_t d[11]={1,1,4,2,0,2,2,1,14336,0,0};
 t.Observe(0,d,4096,0);t.Observe(0,d,4128,0);
 assert(t.rows[0][11]==2 && t.rows[0][12]==0 && t.rows[0][14]==3);
 d[8]=32;t.Observe(0,d,4100,0);assert(t.rows[0][12]==1);
 t.Observe(24,d,0,0);assert(t.dropped==1 && t.rows[0][11]==3);
}'''
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);(p/'test.cpp').write_text(code)
            subprocess.run(['c++','-std=c++17','-I',str(root/'include'),str(p/'test.cpp'),'-o',str(p/'test')],check=True,capture_output=True)
            subprocess.run([str(p/'test')],check=True,capture_output=True)

if __name__=='__main__':unittest.main()
