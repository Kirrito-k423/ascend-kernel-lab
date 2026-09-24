"""DataCopy 的字节契约、失败封闭匹配和原始计时校验；属于 CPU 测试。"""
from dataclasses import asdict, replace
import copy
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
import numpy as np
from akl.datacopy import CopyCase, make_buffers, suite
from akl.datacopy_compare import annotate


class CopyContracts(unittest.TestCase):
    def test_matrix_fits_and_unique(self):
        cases=suite()
        self.assertEqual(len(cases),len({c.name for c in cases}))
        for c in cases:
            self.assertEqual(len(c.pack()),64)
            self.assertLessEqual(c.layout()['ub_working_set_bytes']+320,128*1024)

    def test_pad_load_pitch_and_final_ring(self):
        c=CopyCase('pad',api='DataCopyPad_params',block_bytes=28,blocks=2,gm_gap_bytes=4,
                   gm_offset_bytes=4,ub_offset_bytes=32,batch=2,slots=4,loops=3)
        x,y,expected,defined=make_buffers(c)
        # 最后一轮 group=2 访问 slot 0/1；UB 每块进位32，GM间距32。
        np.testing.assert_array_equal(expected[32:60],x[4:32])
        np.testing.assert_array_equal(expected[64:92],x[36:64])
        np.testing.assert_array_equal(expected[128:156],x[68:96])
        self.assertFalse(defined[60]);self.assertTrue(defined[-1])

    def test_scatter_preserves_offsets_and_gaps(self):
        c=CopyCase('store',direction='UB_GM',block_bytes=32,blocks=2,gm_gap_bytes=480,gm_offset_bytes=32)
        x,y,expected,defined=make_buffers(c)
        np.testing.assert_array_equal(expected[32:64],x[:32])
        np.testing.assert_array_equal(expected[544:576],x[32:64])
        self.assertTrue(np.all(expected[64:544]==0xa5))
        self.assertTrue(defined.all())

    def test_pad_store_uses_padded_ub_rows(self):
        c=CopyCase('store',direction='UB_GM',api='DataCopyPad_params',block_bytes=36,blocks=2,gm_gap_bytes=4)
        x,_,expected,_=make_buffers(c)
        np.testing.assert_array_equal(expected[40:76],x[64:100])
        self.assertTrue(np.all(expected[36:40]==0xa5))

    def test_invalid_layouts(self):
        base=CopyCase('ok')
        for change in (dict(name='../bad'),dict(block_bytes=28),dict(api='DataCopy_count',blocks=2),
                       dict(ub_offset_bytes=4),dict(slots=3,batch=2),dict(slots=4096),
                       dict(block_bytes=65536*32),dict(dtype='bf16'),dict(loops=True),
                       dict(api='DataCopyPad_params',gm_gap_bytes=65536)):
            with self.subTest(change=change),self.assertRaises(ValueError): replace(base,**change).params()

    def test_overload_length_units(self):
        count=CopyCase('count',api='DataCopy_count',dtype='float16',block_bytes=512).layout()
        self.assertEqual((count['api_block_len'],count['api_length_unit']),(256,'elements'))
        blocks=CopyCase('blocks',block_bytes=512).layout()
        self.assertEqual((blocks['api_block_len'],blocks['api_length_unit']),(16,'32B_blocks'))
        pad=CopyCase('pad',api='DataCopyPad_params',block_bytes=28).layout()
        self.assertEqual((pad['api_block_len'],pad['api_length_unit']),(28,'bytes'))

    def test_controls_touch_no_output(self):
        for mode in ('sync_only','empty'):
            _,y,expected,defined=make_buffers(CopyCase('control',control=mode))
            np.testing.assert_array_equal(y,expected);self.assertTrue(defined.all())


class ComparisonContracts(unittest.TestCase):
    def fixture(self):
        case=CopyCase('copy',loops=128)
        environment=dict(soc='test-only',clock_hz=50000000)
        trace=dict(akl_clock=dict(unit='us',verified=True,frequency_hz=50000000),traceEvents=[dict(ph='X',pid=1,tid=1,ts=10,dur=40,name='copy')])
        sidecar=dict(schema='akl.datacopy.shapes.v1',trace_sha256='test-sha',environment=environment,
                     operations=[dict(event_index=0,case=asdict(case),scope='datacopy_loop',boundary='completion',call_count=128)])
        catalog=dict(schema='akl.datacopy.catalog.v1',entries=[dict(id='test',key=dict(environment=environment,case=case.signature()),
                     p50_us_per_call=.2,p95_us_per_call=.3,baseline_kind='empirical_isolated_completion',evidence=dict(test_only=True))])
        return trace,sidecar,catalog

    def test_matched_new_lane_retains_observed(self):
        trace,sidecar,catalog=self.fixture();original=copy.deepcopy(trace)
        out,report=annotate(trace,sidecar,catalog,'test-sha')
        self.assertEqual(trace,original);self.assertEqual(out['traceEvents'][0],original['traceEvents'][0])
        self.assertAlmostEqual(out['traceEvents'][-1]['dur'],25.6)
        self.assertNotEqual(out['traceEvents'][-1]['tid'],1)
        self.assertAlmostEqual(report[0]['actual_over_reference'],40/25.6)

    def test_uncertainty_never_draws_duration(self):
        for kind in ('clock','shape','scope','boundary','environment','calls','ambiguous','default_scale'):
            trace,sidecar,catalog=self.fixture()
            if kind=='clock': trace.pop('akl_clock')
            elif kind=='shape': sidecar['operations'][0].pop('case')
            elif kind=='scope': sidecar['operations'][0]['scope']='TokenCopyToBuffer'
            elif kind=='boundary': sidecar['operations'][0]['boundary']='issue'
            elif kind=='environment': sidecar['environment']=dict(soc='other',clock_hz=50000000)
            elif kind=='calls': sidecar['operations'][0]['call_count']=1
            elif kind=='ambiguous': catalog['entries']*=2
            else: trace['traceEvents'].append(dict(pid=1,args=dict(conversion='default display scale')))
            out,report=annotate(trace,sidecar,catalog,'test-sha')
            with self.subTest(kind=kind):
                self.assertEqual(report[0]['status'],'unmatched');self.assertNotIn('dur',out['traceEvents'][-1])

    def test_binding_and_duplicate_rejected(self):
        trace,sidecar,catalog=self.fixture()
        with self.assertRaises(ValueError): annotate(trace,sidecar,catalog,'wrong-trace')
        sidecar['operations']*=2
        with self.assertRaises(ValueError): annotate(trace,sidecar,catalog,'test-sha')


if __name__ == '__main__': unittest.main()
