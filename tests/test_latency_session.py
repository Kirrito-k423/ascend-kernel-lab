import csv
import subprocess
import threading
import tempfile
import unittest
from pathlib import Path
from akl.latency import LatencyProfile

class Session(unittest.TestCase):
    def run_ranks(self, mode='ok'):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        target=Path(temp.name)/'dispatch_latency.csv'
        if mode=='write': target.mkdir()
        n=4;barrier=threading.Barrier(n,timeout=5);slots=[None]*n
        errors=[None]*n;aborts=[0]*n;begins=[0]*n
        class Dist:
            def __init__(self,rank): self.rank=rank
            def get_rank(self,group): return self.rank
            def get_world_size(self,group): return n
            def all_gather_object(self, values, value, group):
                slots[self.rank]=value;barrier.wait();values[:]=slots;barrier.wait()
        def rank_work(rank):
            def begin(r,sync):
                self.assertEqual((r,sync),(rank,True))
                if mode=='setup' and rank==2: raise RuntimeError('setup injection')
                begins[rank]+=1
            def end():
                if mode=='collect' and rank==2: raise RuntimeError('collect injection')
                if mode=='count' and rank==2: return [1]
                if mode=='nan' and rank==2: return [1,float('nan'),.1]
                return [9, .01*(rank+1), .03*(rank+1)]
            def abort(): aborts[rank]+=1
            warmup=-1 if mode=='settings' and rank==2 else 1
            profile=LatencyProfile(None,begin,end,abort,target,warmup,True,Dist(rank))
            try:
                with profile:
                    if mode=='body' and rank==2: raise ValueError('body injection')
                    if mode=='nested':
                        with self.assertRaises(ValueError): profile.__enter__()
            except BaseException as e: errors[rank]=e
        workers=[threading.Thread(target=rank_work,args=(r,)) for r in range(n)]
        for t in workers:t.start()
        for t in workers:t.join(10)
        self.assertFalse(any(t.is_alive() for t in workers),'collective deadlock')
        return target,errors,aborts,begins

    def test_collective_csv_and_nested_ownership(self):
        for mode in ['ok','nested']:
            target,errors,aborts,begins=self.run_ranks(mode)
            self.assertEqual(errors,[None]*4);self.assertEqual(aborts,[1]*4)
            with target.open() as f: rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),12)
            self.assertEqual(sum(int(r['is_warmup']) for r in rows),4)
            self.assertEqual(sum(int(r['in_average']) for r in rows),8)
            self.assertEqual([float(r['elapsed_us']) for r in rows if r['rank']=='3'],[9000,40,120])

    def test_native_event_lifetime(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            binary=str(Path(tmp)/'latency')
            subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-Werror',
                '-I'+str(root/'include'),'-I'+str(root/'tests/latency_stubs'),
                str(root/'tests/latency_native.cpp'),'-o',binary],check=True)
            subprocess.run([binary],check=True)

    def test_collective_failure_cleanup(self):
        for mode in ['settings','setup','collect','count','nan','body','write']:
            with self.subTest(mode=mode):
                target,errors,aborts,begins=self.run_ranks(mode)
                self.assertTrue(all(errors));self.assertFalse(target.is_file())
                self.assertTrue(all(not isinstance(e,threading.BrokenBarrierError) for e in errors))
                self.assertEqual(aborts,begins)

if __name__=='__main__':unittest.main()
