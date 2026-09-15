"""验证计时数据不能丢失精度、缺失不能伪装为零及跨步oracle。"""
import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"python"))
from akl.cases import Case, MAGIC, WORDS, make_inputs, selected, suite
from akl.trace import decode, duration_ticks
import numpy as np

class Contracts(unittest.TestCase):
    def test_suite_unique_and_valid(self):
        for cores in (1,8,48):
            cases=suite(cores)
            self.assertEqual(len(cases),len({c.name for c in cases}))
            for c in cases: c.params()

    def row(self):
        row=np.zeros((1,WORDS),dtype=np.uint64)
        row[0,:8]=[MAGIC,1,5,0,0,0,0,1]
        for i in range(5):row[0,8+2*i:10+2*i]=[i,2**60+i]
        return row

    def test_large_tick_precision(self):
        events=decode(self.row(),"r",0,0,[0])
        self.assertEqual(events[0]["tick"],str(2**60))
        self.assertEqual(duration_ticks(events,0),1)

    def test_missing_overflow_and_count_rejected(self):
        for index,value in ((0,0),(2,4),(3,1),(6,2),(7,0)):
            row=self.row(); row[0,index]=value
            with self.assertRaises(ValueError):decode(row,"r",0,0,[0])

    def test_nonmonotonic_rejected(self):
        row=self.row();row[0,13]=0
        with self.assertRaises(ValueError):decode(row,"r",0,0,[0])

    def test_gap_is_not_pitch(self):
        case=Case("gap",payload_bytes=32,block_count=2,gap_bytes=480)
        x,mask,expect,indices=make_inputs(case)
        self.assertEqual(expect[0].tolist(),list(range(100,108))+list(range(228,236)))
        self.assertEqual(case.params()["input_stride"],136)

    def test_scatter_gap(self):
        x,mask,expect,indices=make_inputs(Case("write",op=4,payload_bytes=32,block_count=2,gap_bytes=480))
        self.assertEqual(indices[0].tolist(),list(range(8))+list(range(128,136)))
        self.assertEqual(expect[0].tolist(),list(range(100,116)))

    def test_fixed_and_empty_masks(self):
        _,_,expected,_=make_inputs(Case("odd",op=2,pattern=2))
        self.assertEqual(expected[0].tolist(),list(range(101,164,2)))
        _,_,expected,_=make_inputs(Case("none",op=2,pattern=0,custom="none",reduce=1,elements=31,repeats=4))
        self.assertEqual(len(expected[0]),0)

    def test_invalid_configs(self):
        for case in (Case("../bad"),Case("unaligned",gap_bytes=1),Case("large",block_count=4096),Case("mask",op=2,pattern=8)):
            with self.assertRaises(ValueError):case.params()

if __name__=="__main__":unittest.main()
