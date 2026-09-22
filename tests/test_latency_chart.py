"""Chart contracts on synthetic data; no device-performance claim."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import matplotlib.pyplot as plt
from akl.latency_plot import plot_latency

class ChartStyle(unittest.TestCase):
    def test_row_scales_selected_range_and_four_extrema(self):
        rows=[dict(rank=rank,iteration=i,elapsed_us=value,is_warmup=i==0,in_average=i!=0)
              for rank,values in enumerate(((9999,100,101,102),(20000,110,108,109)))
              for i,value in enumerate(values)]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'latency.png'
            with patch('matplotlib.figure.Figure.savefig'), patch('akl.latency_plot.plt.close') as closed:
                plot_latency(rows,path.with_suffix('.csv'),path,last_n=2)
            figures=[args.args[0] for args in closed.call_args_list if hasattr(args.args[0],'axes')]
            try:
                detail, overview=figures
                cells=detail.axes[2].tables[0]
                # Row minima occur in different columns; each row must have its own normalization.
                for row,low_col,high_col in ((1,1,3),(2,2,1),(3,2,3)):
                    low,high=cells[row,low_col].get_facecolor(),cells[row,high_col].get_facecolor()
                    self.assertGreater(low[1],low[0])
                    self.assertGreater(high[0],high[1])
                    self.assertEqual(cells[row,0].get_facecolor(),cells[0,0].get_facecolor())
                axis=overview.axes[0]
                self.assertEqual(axis.get_ylim(),(99.0,111.0))  # [101,109] plus 25%; ignores warmup and launch 1.
                annotations={t.get_gid():t for t in axis.texts}
                expected={'max-max':(3,109),'max-min':(2,108),'min-max':(3,102),'min-min':(2,101)}
                for key,point in expected.items():
                    self.assertEqual(annotations['selected-'+key+'-label'].xy,point)
            finally:
                for figure in figures:plt.close(figure)

    def test_constant_rows_and_zero_range(self):
        rows=[dict(rank=0,iteration=i,elapsed_us=0,is_warmup=False,in_average=True) for i in range(2)]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'latency.png'
            with patch('matplotlib.figure.Figure.savefig'), patch('akl.latency_plot.plt.close') as closed:
                plot_latency(rows,path.with_suffix('.csv'),path,last_n=0)
            figures=[args.args[0] for args in closed.call_args_list if hasattr(args.args[0],'axes')]
            try:
                self.assertEqual(figures[-1].axes[0].get_ylim(),(0,.001))
                cells=figures[0].axes[2].tables[0]
                self.assertEqual(cells[1,0].get_facecolor(),cells[1,1].get_facecolor())
            finally:
                for figure in figures:plt.close(figure)

if __name__=='__main__':unittest.main()
