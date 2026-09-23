"""CPU 合成记录验证；不代表设备实测。"""
from dataclasses import asdict
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'python'))
import numpy as np
from akl.datacopy import CopyCase

class ReportContracts(unittest.TestCase):
    def fixture(self, root):
        import hashlib
        from akl.cases import MAGIC,WORDS
        c=CopyCase('sample')
        folder=root/c.name;folder.mkdir()
        p=c.params();raw=np.zeros((1,WORDS),dtype=np.uint64)
        raw[0,:8]=[MAGIC,1,5,0,0,0,512,1]
        for i,t in enumerate((100,200,201,301,320)):raw[0,8+2*i:10+2*i]=[i,t]
        np.save(folder/'trace-0.npy',raw)
        (folder/'samples.json').write_text(json.dumps([
            dict(launch=0,trace=True,warmup=False,correctness=True,ticks='100'),
            dict(launch=1,trace=False,warmup=False,correctness=True)]))
        manifest=dict(schema='akl.datacopy.v1',status='validated',run_id='test-only',device=0,
            profile=dict(soc='test-only',topology='test-only',memory_scope='local_GM',clock_hz=50000000),
            build=dict(npu_arch='test-only',cann_install='test-only',compiler='test-only',library_sha256='test-only'),
            arguments=dict(samples=1),cases=[dict(case=asdict(c),params=p,layout=c.layout(),status='validated',launches=2,expected_retained=512)])
        for label in ('before','after'):
            (root/f'occupancy-{label}.txt').write_text('test-only')
            manifest[f'occupancy_{label}']=dict(observed_idle=True,sha256=hashlib.sha256(b'test-only').hexdigest())
        (root/'manifest.json').write_text(json.dumps(manifest))
        return manifest

    def test_ticks_and_failed_runs_gate_report(self):
        import tempfile
        from unittest.mock import patch
        from akl.datacopy_report import analyse
        with tempfile.TemporaryDirectory() as d,patch('akl.datacopy_report.render'):
            root=Path(d);manifest=self.fixture(root)
            self.assertAlmostEqual(analyse(root)[0]['p50_us_per_call'],2/128)
            raw=np.load(root/'sample/trace-0.npy');raw[0,15]=0;np.save(root/'sample/trace-0.npy',raw)
            with self.assertRaises(ValueError):analyse(root)
            manifest['status']='failed';(root/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):analyse(root)

    def test_legacy_metadata_not_raw_is_corrected(self):
        import tempfile
        from unittest.mock import patch
        from akl.datacopy_report import analyse
        with tempfile.TemporaryDirectory() as d,patch('akl.datacopy_report.render'):
            root=Path(d);manifest=self.fixture(root)
            manifest['cases'][0]['layout'].pop('api_length_unit')
            (root/'manifest.json').write_text(json.dumps(manifest))
            old=(root/'manifest.json').read_bytes()
            self.assertTrue(analyse(root)[0]['layout_metadata_corrected'])
            self.assertEqual(old,(root/'manifest.json').read_bytes())
            manifest['cases'][0]['layout']['gm_pitch_bytes']+=32
            (root/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):analyse(root)

if __name__ == '__main__': unittest.main()
