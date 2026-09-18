import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import matplotlib.pyplot as plt
from akl.latency_plot import load_latency_rows,plot_latency,load_clock_csv,sanitize_cycles,find_latency_csv

ROOT=Path(__file__).resolve().parents[1]/'results/latency-plots'
ROOT.mkdir(parents=True,exist_ok=True)
class Plot(unittest.TestCase):
    def test_legend_layout_statistics_and_pagination(self):
        original=plt.Figure.savefig
        def checked(fig,path,*args,**kwargs):
            fig.canvas.draw();renderer=fig.canvas.get_renderer()
            chart,legends=fig.axes[1],fig.axes[2]
            box=legends.get_legend().get_window_extent(renderer)
            self.assertFalse(box.overlaps(chart.get_window_extent(renderer)))
            self.assertGreaterEqual(box.x0,0);self.assertLessEqual(box.x1,fig.bbox.x1)
            self.assertGreaterEqual(box.y0,0);self.assertLessEqual(box.y1,fig.bbox.y1)
            width,height=fig.get_size_inches();self.assertLess(max(width,height)/min(width,height),2)
            self.assertGreater(chart.get_ylim()[1],max(y for p in chart.collections for _,y in p.get_offsets()) if chart.collections else 0)
            return original(fig,path,*args,**kwargs)
        for n in [1,64,128,257]:
            folder=ROOT/f'preview-{n}';folder.mkdir(exist_ok=True);csvpath=folder/'dispatch_latency.csv'
            with csvpath.open('w',newline='') as f:
                w=csv.writer(f);w.writerow(['rank','iteration','elapsed_us','is_warmup','in_average'])
                for rank in range(n):
                    w.writerow([rank,0,1200,1,0])
                    for it in range(1,6):w.writerow([rank,it,240+rank+it,0,1])
            rows=load_latency_rows(csvpath);output=folder/'dispatch_latency_scatter.png'
            with patch.object(plt.Figure,'savefig',checked):means,count=plot_latency(rows,csvpath,output)
            summary=json.loads(output.with_name('dispatch_latency_scatter_summary.json').read_text())
            self.assertEqual(count,n*5);self.assertEqual(summary['experiment_us'],243+(n-1)/2)
            self.assertEqual(summary['fastest']['mean_us'],243)
            self.assertEqual(summary['slowest']['mean_us'],243+n-1)
            self.assertEqual(len(list(folder.glob('*.png'))),(n+127)//128)

    def test_equal_rank_weight_and_bad_samples(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);path=folder/'dispatch_latency.csv'
            path.write_text('rank,iteration,elapsed_us,is_warmup,in_average\n0,0,1000,1,0\n0,1,10,0,1\n1,0,20,0,1\n1,1,40,0,1\n')
            plot_latency(load_latency_rows(path),path,folder/'p.png')
            self.assertEqual(json.loads((folder/'p_summary.json').read_text())['experiment_us'],20)
            for records in ['0,0,nan,0,1','0,0,-1,0,1','0,0,1,1,1','0,0,1,0,1\n0,0,2,0,1']:
                path.write_text('rank,iteration,elapsed_us,is_warmup,in_average\n'+records+'\n')
                with self.assertRaises(ValueError):load_latency_rows(path)
            for name in ['a','b']:
                (folder/name).mkdir();(folder/name/'dispatch_latency.csv').write_text('')
            path.unlink()
            with self.assertRaisesRegex(ValueError,'select one'):find_latency_csv(folder,'dispatch_latency.csv')
            clock=folder/'clock.csv';clock.write_text('rank,aiv_id,time_1_cycles\n0,0,600000\n')
            _,values,_,_=load_clock_csv(clock);self.assertEqual(values[0,0],600000)
            with self.assertRaises(ValueError):sanitize_cycles(-values,clock)

if __name__=='__main__':unittest.main()
