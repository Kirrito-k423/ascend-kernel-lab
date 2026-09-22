"""核内 latency 合约与元数据出图，CPU 合成数据。"""
import csv
from pathlib import Path
import subprocess
import tempfile
import unittest
from akl.latency import LatencyProfile
from akl.latency_plot import load_latency_rows, plot_latency

class LocalCollective:
    def get_rank(self, group): return 0
    def get_world_size(self, group): return 1
    def all_gather_object(self, values, value, group): values[:] = [value]

class KernelLatency(unittest.TestCase):
    def test_native_contract(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            binary = str(Path(folder)/'native')
            subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-Werror',
                '-I'+str(root/'include'),'-I'+str(root/'tests/kernel_latency_stubs'),
                str(root/'tests/kernel_latency.cpp'),'-o',binary],check=True)
            subprocess.run([binary],check=True)

    def test_metadata_csv_and_plot(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'dispatch_latency.csv'
            profile = LatencyProfile(None,lambda *a:None,lambda:[.5,.02,.03],lambda:None,
                path,warmup=1,collective=LocalCollective(),
                metadata={'measurement':'kernel','clock_hz':'50000000'})
            with profile: pass
            with path.open() as stream: saved=list(csv.DictReader(stream))
            self.assertEqual([r['measurement'] for r in saved],['kernel']*3)
            self.assertEqual(saved[1]['elapsed_us'],'20.000000000')
            rows=load_latency_rows(path)
            target=Path(folder)/'latency.png'
            means,count=plot_latency(rows,path,target,last_n=0)
            self.assertEqual(means,{0:25});self.assertEqual(count,2)
            self.assertGreater(target.stat().st_size,1000)
            self.assertIn('outer barriers excluded',target.with_suffix('.svg').read_text())
            rows[1]['measurement']='event'
            with self.assertRaisesRegex(ValueError,'mixed'): plot_latency(rows,path,target)

if __name__=='__main__': unittest.main()
