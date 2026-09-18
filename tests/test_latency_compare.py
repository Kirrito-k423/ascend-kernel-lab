"""CPU 编译库 + 新进程会话的真实配对流程；不是 NPU latency。"""
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from akl.latency_compare import run

PROGRAM = '''import ctypes, os
from pathlib import Path
from akl.latency import LatencyProfile
library = ctypes.CDLL(str(Path('mode.so').resolve()))
library.trace_enabled.restype = ctypes.c_bool
enabled = library.trace_enabled()
class Dist:
    def get_world_size(self, group): return 64
    def get_rank(self, group): return 0
    def all_gather_object(self, values, value, group):
        for rank in range(64):
            if isinstance(value, dict) and 'samples' in value:
                values[rank] = dict(value, samples=[v + rank*.0001 for v in value['samples']])
            else: values[rank] = value
samples = [1., .012 if enabled else .01, .024 if enabled else .02]
if os.getenv('MISMATCH') and not enabled: samples.append(.01)
with LatencyProfile(None, lambda *a: None, lambda: samples, lambda: None,
                    'ignored/dispatch_latency.csv', warmup=1, collective=Dist(),
                    trace_enabled=lambda: enabled): pass
'''

class Comparison(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root/'probe.py').write_text(PROGRAM)
        (self.root/'mode.cpp').write_text('extern "C" bool trace_enabled() { return MODE; }\n')
        self.build = 'flag=0; if [[ "$DEBUG_CLOCK_ON" == ON ]]; then flag=1; fi; clang++ -shared -fPIC -DMODE="$flag" mode.cpp -o mode.so'
        self.env = patch.dict(os.environ, PYTHONPATH=str(Path(__import__('akl').__file__).parent.parent))
        self.env.start(); self.addCleanup(self.env.stop)

    def args(self, name='result', **changes):
        options = dict(cwd=str(self.root), output=str(self.root/name), build=self.build,
                       run=f'{shlex.quote(sys.executable)} probe.py', build_flag='DEBUG_CLOCK_ON',
                       csv_name='dispatch_latency.csv', last_n=0, timeout=60)
        return argparse.Namespace(**(options | changes))

    def invoke(self, args):
        with redirect_stdout(io.StringIO()): return run(args)

    def test_actual_pair_and_artifacts(self):
        result = self.invoke(self.args())
        self.assertEqual(result['status'], 'complete')
        self.assertAlmostEqual(result['variants']['ON']['experiment_us'], 21.15)
        self.assertAlmostEqual(result['variants']['OFF']['experiment_us'], 18.15)
        self.assertAlmostEqual(result['delta_us'], 3)
        self.assertAlmostEqual(result['overhead_percent'], 3/18.15*100)
        for mode in ('on', 'off'):
            root = self.root/'result'
            self.assertTrue((root/f'latency_{mode}.png').is_file())
            svg = (root/f'latency_{mode}.svg').read_text()
            self.assertIn(f'DebugClock {mode.upper()}', svg)
            self.assertIn('warmup-0', svg)
            self.assertEqual(svg.count('id="rank-mean-'), 64)
            self.assertEqual(result['variants'][mode.upper()]['samples'], 192)
            self.assertTrue((root/f'trace_{mode}'/'build.log').is_file())
        self.assertFalse((self.root/'ignored').exists())
        before = (root/'comparison.json').read_bytes()
        with self.assertRaises(FileExistsError): self.invoke(self.args())
        self.assertEqual((root/'comparison.json').read_bytes(), before)

    def test_stale_binary_is_rejected_by_session(self):
        build = 'clang++ -shared -fPIC -DMODE=1 mode.cpp -o mode.so'
        with self.assertRaises(RuntimeError): self.invoke(self.args(build=build))
        root = self.root/'result'
        self.assertIn('expected OFF', (root/'trace_off/run.log').read_text())
        self.assertEqual(json.loads((root/'comparison.json').read_text())['status'], 'failed')
        self.assertFalse((root/'latency_off.png').exists())

    def test_sample_mismatch_and_build_failure(self):
        with patch.dict(os.environ, MISMATCH='1'):
            with self.assertRaisesRegex(ValueError, 'masks differ'): self.invoke(self.args())
        with self.assertRaisesRegex(RuntimeError, 'exited 7'):
            self.invoke(self.args('failure', build='exit 7'))
        self.assertFalse((self.root/'failure/trace_on/run.log').exists())
        self.assertFalse((self.root/'failure/trace_off').exists())

    def test_multiple_sessions_and_timeout(self):
        with self.assertRaises(RuntimeError):
            self.invoke(self.args(run=f'{shlex.quote(sys.executable)} probe.py; {shlex.quote(sys.executable)} probe.py'))
        self.assertIn('one profiling session', (self.root/'result/trace_on/run.log').read_text())
        with self.assertRaises(subprocess.TimeoutExpired):
            self.invoke(self.args('timeout', build='sleep 30', timeout=.05))
        self.assertEqual(json.loads((self.root/'timeout/comparison.json').read_text())['status'], 'failed')

    def test_mode_disagreement_is_collective(self):
        from akl.latency import LatencyProfile
        class Dist:
            def get_world_size(self, group): return 2
            def all_gather_object(self, values, value, group):
                values[:] = [value, (*value[:-1], 'OFF')]
        begun = []
        with patch.dict(os.environ, AKL_TRACE_MODE='ON'):
            profile = LatencyProfile(None, lambda *a: begun.append(1), lambda: [], lambda: None,
                                     'unused.csv', collective=Dist(), trace_enabled=lambda: True)
            with self.assertRaisesRegex(ValueError, 'identical'): profile.__enter__()
        self.assertEqual(begun, [])

if __name__ == '__main__': unittest.main()
