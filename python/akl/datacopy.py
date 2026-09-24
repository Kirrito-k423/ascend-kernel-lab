"""单 AIV DataCopy 参数、字节语义和可观察输出；不依赖 NPU。"""
from dataclasses import asdict, dataclass
import re
import struct

SCHEMA = 'akl.datacopy.v1'
APIS = ('DataCopy_count', 'DataCopy_params', 'DataCopyPad_params')
FIELDS = ('direction api element_bytes block_bytes blocks gm_gap_bytes gm_offset_bytes '
          'ub_offset_bytes loops batch control slots gm_stride_bytes ub_stride_bytes reserved0 reserved1').split()


def align(value, size=32):
    return (value + size - 1) // size * size


@dataclass(frozen=True)
class CopyCase:
    name: str
    direction: str = 'GM_UB'
    api: str = 'DataCopy_params'
    dtype: str = 'uint32'
    block_bytes: int = 512
    blocks: int = 1
    gm_gap_bytes: int = 0
    gm_offset_bytes: int = 0
    ub_offset_bytes: int = 0
    loops: int = 128
    batch: int = 1
    slots: int = 1
    control: str = 'payload'
    windows: int = 1

    def params(self):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', self.name):
            raise ValueError('case 名称必须是安全文件名')
        numeric = ('block_bytes', 'blocks', 'gm_gap_bytes', 'gm_offset_bytes', 'ub_offset_bytes', 'loops', 'batch', 'slots', 'windows')
        if any(type(getattr(self, k)) is not int or not 0 <= getattr(self, k) <= 0xffffffff for k in numeric):
            raise ValueError('整数参数必须为 uint32')
        if self.direction not in ('GM_UB', 'UB_GM') or self.api not in APIS or self.dtype not in ('uint32', 'float16', 'bfloat16', 'float32', 'uint8'):
            raise ValueError('未实现的方向/API/dtype')
        if self.control not in ('payload', 'sync_only', 'empty'):
            raise ValueError('未实现的 control')
        if not 1 <= self.loops <= 100000 or not 1 <= self.batch <= 64:
            raise ValueError('loops/batch 越界')
        if self.windows not in (1, 2) or self.loops < self.windows:
            raise ValueError('windows 只支持1（每批完成）/2（双窗口）；每个窗口至少执行一次')
        if not self.batch * self.windows <= self.slots <= 65536 or self.slots % self.batch or self.loops * self.batch < self.slots:
            raise ValueError('slots 必须为 batch 整数倍且每个 slot 至少访问一次')
        if not 1 <= self.blocks <= 4095 or not self.block_bytes:
            raise ValueError('block 参数超过本实现边界')
        element = 2 if self.dtype in ('float16', 'bfloat16') else 1 if self.dtype == 'uint8' else 4
        if any(v % element for v in (self.block_bytes, self.gm_gap_bytes, self.gm_offset_bytes)):
            raise ValueError('GM 参数必须按 dtype 对齐')
        if self.ub_offset_bytes % 32:
            raise ValueError('UB 地址必须按 32B 对齐')
        pad = self.api == 'DataCopyPad_params'
        # 同为 uint16 blockLen，Pad 以字节计，普通 params 以 32B 块计。
        # count 路径最终也使用 DMA 块长度；这里保留相同的单段上界。
        if self.block_bytes > (65535 if pad else 65535 * 32):
            raise ValueError('block 长度超出对应重载字段')
        if not pad and any(v % 32 for v in (self.block_bytes, self.gm_gap_bytes, self.gm_offset_bytes)):
            raise ValueError('DataCopy 必须按 32B 对齐；非对齐实验使用 DataCopyPad')
        if self.gm_gap_bytes > (65535 if pad else 65535 * 32):
            raise ValueError('GM gap 超出重载字段')
        if self.api == 'DataCopy_count' and (self.blocks != 1 or self.gm_gap_bytes):
            raise ValueError('count 重载只支持单段连续拷贝')
        gm_stride = align(self.gm_offset_bytes + self.blocks * self.block_bytes + (self.blocks - 1) * self.gm_gap_bytes)
        ub_stride = align(self.ub_offset_bytes + self.blocks * align(self.block_bytes))
        # 这里只限制主机分配；真实 UB 容量由 runner 查询后逐项检查。
        # 不能用固定 128KiB 软件预算截断不同芯片的吞吐扫描。
        if ub_stride * self.batch * self.windows + 320 > 256 * 1024 * 1024:
            raise ValueError('UB 布局超过 256MiB 主机分配上限')
        if gm_stride * self.slots > 256 * 1024 * 1024:
            raise ValueError('GM 工作集超过 256MiB 实验上限')
        return dict(direction=int(self.direction == 'UB_GM'), api=APIS.index(self.api), element_bytes=element,
                    block_bytes=self.block_bytes, blocks=self.blocks, gm_gap_bytes=self.gm_gap_bytes,
                    gm_offset_bytes=self.gm_offset_bytes, ub_offset_bytes=self.ub_offset_bytes,
                    loops=self.loops, batch=self.batch, control=('payload', 'sync_only', 'empty').index(self.control),
                    slots=self.slots, gm_stride_bytes=gm_stride, ub_stride_bytes=ub_stride,
                    reserved0={'bfloat16':1, 'float32':2}.get(self.dtype,0), reserved1=self.windows - 1)

    def pack(self):
        p = self.params()
        return struct.pack('<16I', *(p[k] for k in FIELDS))

    def signature(self):
        """不跨 shape、同步、缓存、dtype 推断；循环数保留以约束摊销条件。"""
        self.params()
        # 默认模式继续匹配早期归档；双窗口必须有独立的签名。
        return {k: v for k, v in asdict(self).items() if k != 'name' and not (k == 'windows' and v == 1)}

    def layout(self):
        p = self.params()
        return dict(payload_bytes_per_call=self.block_bytes * self.blocks,
                    calls=self.loops * self.batch, aiv_count=1, implementation='AscendC_MTE',
                    measurement=('pipelined_completion' if self.windows == 2 else
                                 'serialized_completion' if self.batch == 1 else 'batched_completion'),
                    gm_working_set_bytes=p['gm_stride_bytes'] * self.slots,
                    ub_working_set_bytes=p['ub_stride_bytes'] * self.batch * self.windows,
                    cache_policy='default; reused ring; coldness unverified',
                    gm_gap_bytes=self.gm_gap_bytes, gm_pitch_bytes=self.block_bytes + self.gm_gap_bytes,
                    ub_pitch_bytes=align(self.block_bytes),
                    api_block_len=(self.block_bytes // p['element_bytes'] if self.api == 'DataCopy_count' else
                                   self.block_bytes if self.api.endswith('Pad_params') else self.block_bytes // 32),
                    api_length_unit=('elements' if self.api == 'DataCopy_count' else
                                     'bytes' if self.api.endswith('Pad_params') else '32B_blocks'),
                    api_gm_stride=self.gm_gap_bytes if self.api.endswith('Pad_params') else self.gm_gap_bytes // 32)


def make_buffers(case):
    import numpy as np
    p = case.params()
    gm_size = p['gm_stride_bytes'] * case.slots
    ub_size = p['ub_stride_bytes'] * case.batch * case.windows
    size = gm_size if case.direction == 'GM_UB' else ub_size
    # FP16 使用有限正规数的原始位模式，避免 NaN 表示差异。
    if case.dtype in ('float16', 'bfloat16'):
        modulus, base = (1024, 0x3800) if case.dtype=='float16' else (128, 0x3f00)
        x = (np.arange(size // 2, dtype=np.uint32) % modulus + base).astype(np.uint16).view(np.uint8)
    else:
        x = (np.arange(size // 4, dtype=np.uint32) * 2654435761 + 100).astype(np.uint32).view(np.uint8)
    y = np.full((ub_size if case.direction == 'GM_UB' else gm_size) + 128, 0xa5, dtype=np.uint8)
    expected = y.copy()
    defined = np.ones(len(y), dtype=bool)
    if case.control != 'payload':
        return x, y, expected, defined
    if case.direction == 'GM_UB':
        # Pad 的补齐区没有值语义，只有有效 payload 和 GM 输出保护区用于判定。
        defined[:ub_size] = False
        for j in range(case.batch * case.windows):
            # 每个 UB 窗口最后写入的 group；奇数 loops 不能把两窗口都当最后一批。
            group = case.loops - 1 - (case.loops - 1 - j // case.batch) % case.windows
            slot = (group * case.batch + j % case.batch) % case.slots
            for b in range(case.blocks):
                src = slot * p['gm_stride_bytes'] + case.gm_offset_bytes + b * (case.block_bytes + case.gm_gap_bytes)
                dst = j * p['ub_stride_bytes'] + case.ub_offset_bytes + b * align(case.block_bytes)
                expected[dst:dst + case.block_bytes] = x[src:src + case.block_bytes]
                defined[dst:dst + case.block_bytes] = True
    else:
        for slot in range(case.slots):
            # 最后一次写该 GM slot 的 group 决定使用哪个 UB 窗口。
            group = case.loops - 1 - (case.loops - 1 - slot // case.batch) % (case.slots // case.batch)
            j = (group % case.windows) * case.batch + slot % case.batch
            for b in range(case.blocks):
                src = j * p['ub_stride_bytes'] + case.ub_offset_bytes + b * align(case.block_bytes)
                dst = slot * p['gm_stride_bytes'] + case.gm_offset_bytes + b * (case.block_bytes + case.gm_gap_bytes)
                expected[dst:dst + case.block_bytes] = x[src:src + case.block_bytes]
    return x, y, expected, defined


def suite():
    cases = []
    for direction in ('GM_UB', 'UB_GM'):
        for api in APIS:
            for n in (32, 64, 128, 256, 512, 1024, 4096, 8192, 14336, 15872, 32768):
                for batch in (1, 4):
                    c = CopyCase(f'{direction}_{api}_{n}_b{batch}', direction=direction, api=api,
                                 block_bytes=n, batch=batch, slots=batch)
                    try: c.params()
                    except ValueError: continue
                    # 保留已发布基础矩阵；容量扩展由 datacopy_sweep 显式规划。
                    if c.layout()['ub_working_set_bytes'] + 320 > 128 * 1024: continue
                    cases.append(c)
        for n, blocks, gap in ((32, 16, 480), (480, 16, 32), (480, 31, 32)):
            cases.append(CopyCase(f'{direction}_strided_{n}_{blocks}_{gap}', direction=direction,
                                  block_bytes=n, blocks=blocks, gm_gap_bytes=gap))
        for n in (4, 16, 28, 36):
            cases.append(CopyCase(f'{direction}_pad_{n}', direction=direction, api='DataCopyPad_params', block_bytes=n))
        for batch in (1, 4):
            for control in ('sync_only', 'empty'):
                cases.append(CopyCase(f'{direction}_{control}_b{batch}', direction=direction, batch=batch,
                                      slots=batch, control=control))
    return cases
