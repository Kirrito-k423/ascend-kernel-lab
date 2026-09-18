"""显式多 rank 计时会话：计时边界由 begin/end/abort 后端定义，保留逐轮样本。"""
import csv
import math
import os
from pathlib import Path
import tempfile


class LatencyProfile:
    def __init__(self, group, begin, end, abort, output, warmup=10,
                 synchronize_start=True, collective=None):
        if collective is None:
            import torch.distributed as collective
        self.dist = collective
        self.group = group
        self.warmup = warmup
        self.synchronize_start = synchronize_start
        self.output = Path(output)
        self._begin, self._end, self._abort = begin, end, abort
        self.samples = []
        self._active = False

    def _all(self, value):
        values = [None] * self.dist.get_world_size(self.group)
        self.dist.all_gather_object(values, value, group=self.group)
        return values

    def __enter__(self):
        error = 'profiling context already active' if self._active else None
        if type(self.warmup) is not int or self.warmup < 0:
            error = 'warmup must be a nonnegative integer'
        if type(self.synchronize_start) is not bool:
            error = 'synchronize_start must be bool'
        settings = self._all((error, self.warmup, self.synchronize_start))
        if any(value[0] for value in settings):
            raise ValueError(f'invalid profiling settings: {settings}')
        if any(value != settings[0] for value in settings):
            raise ValueError('all ranks must use identical profiling settings')
        started, error = False, None
        try:
            self._begin(self.dist.get_rank(self.group), self.synchronize_start)
            started = True
        except Exception as caught:
            error = f'{type(caught).__name__}: {caught}'
        try:
            errors = self._all(error)
            if any(errors):
                raise RuntimeError(f'latency session setup failed: {errors}')
        except BaseException:
            if started:
                self._abort()
            raise
        self._active = True
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.samples = []
        error = f'{exc_type.__name__}: {exc_value}' if exc_type else None
        try:
            if exc_type is None:
                self.samples = self._end()
        except Exception as caught:
            error = f'{type(caught).__name__}: {caught}'
        finally:
            self._active = False
            try:
                self._abort()
            except Exception as caught:
                error = error or f'{type(caught).__name__}: {caught}'
        results = self._all({'error': error, 'samples': self.samples})
        errors = [r['error'] for r in results]
        if any(errors):
            raise RuntimeError(f'latency collection failed: {errors}') from exc_value
        lengths = [len(r['samples']) for r in results]
        if len(set(lengths)) != 1 or lengths[0] <= self.warmup:
            raise ValueError(f'need equal call counts exceeding warmup={self.warmup}, got {lengths}')
        if any(not math.isfinite(ms) or ms < 0 for r in results for ms in r['samples']):
            raise ValueError('invalid device event duration')
        write_error = None
        if self.dist.get_rank(self.group) == 0:
            try:
                self._write(results)
            except Exception as caught:
                write_error = f'{type(caught).__name__}: {caught}'
        errors = self._all(write_error)
        if any(errors):
            raise RuntimeError(f'latency CSV write failed: {errors}')
        return False

    def _write(self, results):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', newline='', dir=self.output.parent, delete=False) as stream:
                temporary = Path(stream.name)
                writer = csv.writer(stream)
                writer.writerow(['rank', 'iteration', 'elapsed_ms', 'elapsed_us', 'is_warmup', 'in_average'])
                for i in range(len(results[0]['samples'])):
                    for rank, result in enumerate(results):
                        ms = result['samples'][i]
                        warmup = i < self.warmup
                        writer.writerow([rank, i, f'{ms:.9f}', f'{ms * 1000:.9f}', int(warmup), int(not warmup)])
            os.replace(temporary, self.output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
