"""使用仓的 Python 打包和旧命令接线检查；不导入 torch/CANN 扩展。"""
import ast
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from setuptools import Distribution,find_packages
from setuptools.command.build_py import build_py

REPO=Path(os.environ.get('DEEPEP_SOURCE','__missing_deepep__'))
@unittest.skipUnless((REPO/'setup.py').is_file(), 'set DEEPEP_SOURCE to test consumer packaging')
class Integration(unittest.TestCase):
    def test_package_bundles_shared_modules_and_assets(self):
        tree=ast.parse((REPO/'setup.py').read_text())
        call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='setup')
        keys={'packages','package_dir','package_data'}
        old=os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(REPO)
                options={k.arg:eval(compile(ast.Expression(k.value),'setup.py','eval'),{'find_packages':find_packages}) for k in call.keywords if k.arg in keys}
                distribution=Distribution(options);distribution.script_name='setup.py'
                cmd=build_py(distribution);cmd.build_lib=tmp;cmd.ensure_finalized();cmd.run()
            finally:os.chdir(old)
            for name in ('latency.py','latency_plot.py','semantic.py','timeline.js','batch.html'):
                self.assertTrue((Path(tmp)/'akl'/name).is_file(),name)
            env=dict(os.environ,PYTHONPATH=tmp)
            subprocess.run([os.sys.executable,'-c','from akl.latency import LatencyProfile; from akl.latency_plot import load_latency_rows'],env=env,cwd=tmp,check=True)

    def test_legacy_cli_and_clean_includes(self):
        script=REPO/'test/analyze_dispatch_time.py'
        subprocess.run([os.sys.executable,str(script),'--help'],check=True,stdout=subprocess.DEVNULL)
        source=(REPO/'kernels/elastic_dispatch.cpp').read_bytes()
        for old in [b'elastic_dispatch_clock_host.h',b'elastic_dispatch_latency_host.h',b'utils/debug/asc_printf.h',b'debug_clock::']:
            self.assertNotIn(old,source)
        self.assertEqual(source.count(b'\n'),source.count(b'\r\n'))
        self.assertLess(source.index(b'latency.Start(stream);'),source.index(b'elastic_dispatch_kernel<<<'))
        self.assertLess(source.index(b'elastic_dispatch_kernel<<<'),source.index(b'latency.Finish(stream);'))
        self.assertLess(source.index(b'latency.Finish(stream);'),source.index(b'trace.Export('))

if __name__=='__main__':unittest.main()
