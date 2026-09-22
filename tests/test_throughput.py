"""CPU contracts and synthetic reports; no NPU throughput claim."""
import csv
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from akl.latency import LatencyProfile
from akl.latency_plot import load_latency_rows, plot_latency

class Collective:
    def get_rank(self, group): return 0
    def get_world_size(self, group): return 2
    def all_gather_object(self, values, value, group):
        values[:] = [value, value]
        if isinstance(value, dict) and value.get('work'):
            values[1] = dict(value, work=[[2*a, 2*b] for a,b in value['work']])

class Throughput(unittest.TestCase):
    def test_native(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            binary = str(Path(directory)/'native')
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',
                '-I'+str(root/'include'),'-I'+str(root/'tests/throughput_stubs'),
                str(root/'tests/throughput.cpp'),'-o',binary],check=True)
            subprocess.run([binary],check=True)

    def test_csv_and_dual_axis(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);csv_path=root/'latency.csv';image=root/'latency.png'
            profile=LatencyProfile(None,lambda *a:None,lambda:[10,.02,.04],lambda:None,csv_path,
                warmup=1,collective=Collective(),work=lambda:[[999999999,1],[1000000,0],[3000000,1000000]],
                metadata={'measurement':'kernel','clock_hz':'50000000','work_kind':'token_payload'})
            with profile: pass
            with csv_path.open() as stream: saved=list(csv.DictReader(stream))
            self.assertEqual(saved[2]['processed_gbps'],'50.0')
            rows=load_latency_rows(csv_path);plot_latency(rows,csv_path,image,last_n=0)
            summary=json.loads((root/'latency_summary.json').read_text())
            self.assertAlmostEqual(summary['throughput']['rank_gbps']['processed']['0'],4000000/60000)
            self.assertAlmostEqual(summary['throughput']['mean_gbps']['processed'],100)
            svg=image.with_suffix('.svg').read_text()
            for label in ('Payload throughput (GB/s)','processed / right axis','sent / right axis','warmup-0','rank-mean-1'):
                self.assertIn(label,svg)
            plot_latency(rows,csv_path,image,last_n=1)
            self.assertEqual(json.loads((root/'latency_summary.json').read_text())['throughput']['mean_gbps']['processed'],112.5)
            rows[1]['work_kind']='other'
            with self.assertRaisesRegex(ValueError,'definitions'):plot_latency(rows,csv_path,image)

    def test_undefined_and_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'latency.csv'
            def run(work):
                profile=LatencyProfile(None,lambda *a:None,lambda:[0],lambda:None,path,
                    warmup=0,collective=Collective(),work=lambda:work)
                with profile:pass
            run([[0,0]])
            with path.open() as stream: self.assertEqual(list(csv.DictReader(stream))[0]['processed_gbps'],'')
            plot_latency(load_latency_rows(path),path,path.with_suffix('.png'),last_n=0)
            summary=json.loads(path.with_name('latency_summary.json').read_text())
            self.assertIsNone(summary['throughput']['mean_gbps']['processed'])
            with self.assertRaises(ValueError):run([[-1,0]])
            with self.assertRaises(ValueError):run([])

if __name__=='__main__':unittest.main()
