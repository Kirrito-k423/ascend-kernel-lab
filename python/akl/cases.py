"""实验参数、CPU oracle 与 ABI；不依赖 NPU。"""
from dataclasses import dataclass, asdict
import struct
import re

FIELD_NAMES = "op elements repeats pattern reduce loops payload_bytes block_count gap_bytes delay_ticks input_stride output_stride src0_block_stride src0_repeat_stride src1_repeat_stride".split()
MAGIC = 0x414B4C5452433031
CAPACITY, WORDS = 16, 40

@dataclass
class Case:
    name: str
    op: int = 1
    cores: int = 1
    loops: int = 64
    payload_bytes: int = 512
    block_count: int = 16
    gap_bytes: int = 0
    elements: int = 64
    repeats: int = 1
    pattern: int = 1
    reduce: int = 0
    custom: str = "even"
    delay_ticks: int = 0
    src0_block_stride: int = 1
    src0_repeat_stride: int = 8
    src1_repeat_stride: int = 1

    def params(self):
        if not re.fullmatch(r'[A-Za-z0-9_-]+',self.name): raise ValueError('实验名称只允许字母、数字、下划线和连字符')
        for k, v in asdict(self).items():
            if k not in ("name", "custom") and (type(v) is not int or v < 0 or v > 0xffffffff):
                raise ValueError(f"参数 {k} 必须是 uint32")
        if self.op not in (0, 1, 2, 4) or not 1 <= self.cores <= 128 or not 1 <= self.loops <= 100000:
            raise ValueError("不支持的操作、核数或循环次数")
        if not 0 <= self.delay_ticks <= 50000:
            raise ValueError("每核注入延迟超过实验上限")
        if self.reduce not in (0, 1):
            raise ValueError("reduce 必须为0或1")
        if self.op == 2:
            if not 0 <= self.pattern <= 7 or not 1 <= self.repeats <= 64:
                raise ValueError("GatherMask 模式或 repeat 越界")
            if not 1 <= self.elements <= 64 or (not self.reduce and self.elements != 64):
                raise ValueError("初版每 repeat 支持1..64个uint32；普通模式固定64")
            if not 1 <= self.src0_block_stride <= 8 or not 1 <= self.src0_repeat_stride <= 128:
                raise ValueError("GatherMask 源步长超出初版范围")
            if not 0 <= self.src1_repeat_stride <= 2:
                raise ValueError("自定义掩码步长超出初版范围")
            if self.custom not in ("none", "all", "even", "random"):
                raise ValueError("未知自定义掩码")
            last = (self.repeats-1)*self.src0_repeat_stride*8 + ((self.elements-1)//8)*self.src0_block_stride*8 + (self.elements-1)%8
            ins = outs = max(((last+8)//8)*8, ((self.elements*self.repeats+7)//8)*8)
        else:
            if self.payload_bytes < 32 or self.payload_bytes % 32 or self.gap_bytes % 32:
                raise ValueError("DataCopy payload与gap必须为32字节整数倍")
            if not 1 <= self.block_count <= 4095 or self.payload_bytes//32 > 65535 or self.gap_bytes//32 > 65535:
                raise ValueError("DataCopy 参数字段越界")
            compact = self.block_count*self.payload_bytes//4
            span = (self.block_count*self.payload_bytes+(self.block_count-1)*self.gap_bytes)//4
            ins, outs = (compact, span) if self.op == 4 else (span, compact)
        # 与本轮设备端两块UB和mask区预算一致；不替代运行时UB容量查询。
        if max(ins, outs)*4 > 65536:
            raise ValueError("初版单通道源/目标空间不能超过64KiB")
        p = {k: getattr(self, k) for k in FIELD_NAMES if hasattr(self, k)}
        p.update(input_stride=ins, output_stride=outs)
        return p

    def pack(self):
        p = self.params()
        return struct.pack("<15I", *(p[k] for k in FIELD_NAMES))

def selected(pattern, index):
    if pattern == 7: return True
    if pattern in (1, 2): return index % 2 == pattern-1
    if pattern in (3, 4, 5, 6): return index % 4 == pattern-3
    raise ValueError("固定掩码必须为1..7")

def make_inputs(case):
    import numpy as np
    p = case.params()
    x = np.arange(case.cores*p["input_stride"], dtype=np.uint32) + 100
    mask = np.zeros(1024, dtype=np.uint32)
    rng = np.random.default_rng(20260915)
    for j in range(len(mask)):
        mask[j] = {"none": 0, "all": 0xffffffff, "even": 0x55555555}.get(case.custom, 0)
        if case.custom == "random": mask[j] = rng.integers(0, 2**32, dtype=np.uint32)
    expected, indices = [], []
    for core in range(case.cores):
        base = core*p["input_stride"]
        if case.op == 2:
            ids = []
            for r in range(case.repeats):
                for e in range(case.elements):
                    take = selected(case.pattern, e) if case.pattern else bool(
                        int(mask[r*case.src1_repeat_stride*8+e//32]) & (1 << (e%32)))
                    if take: ids.append(base+r*case.src0_repeat_stride*8+(e//8)*case.src0_block_stride*8+e%8)
            expected.append(x[ids])
            indices.append(np.arange(len(ids)))
        elif case.op in (1, 4):
            n = case.payload_bytes//4
            pitch = (case.payload_bytes+case.gap_bytes)//4
            srcids = [base+b*(n if case.op == 4 else pitch)+e for b in range(case.block_count) for e in range(n)]
            dstids = [b*(pitch if case.op == 4 else n)+e for b in range(case.block_count) for e in range(n)]
            expected.append(x[srcids])
            indices.append(np.array(dstids))
        else:
            expected.append(np.zeros(0, dtype=np.uint32))
            indices.append(np.zeros(0, dtype=int))
    return x, mask, expected, indices

def suite(max_cores, smoke=False):
    cores = sorted(set([1, min(8, max_cores), max_cores]))
    if smoke:
        return [Case("copy_smoke", cores=min(8,max_cores)),
                Case("gather_smoke", op=2, cores=min(8,max_cores)),
                Case("custom_smoke", op=2, pattern=0, custom="random", cores=min(8,max_cores))]
    cases = [Case(f"empty_c{n}", op=0, cores=n) for n in cores]
    for n in cores:
        for op, direction in ((1, "gm_ub"), (4, "ub_gm")):
            for payload, gap in ((32,0), (32,480), (480,32), (512,0), (512,512), (1024,0)):
                cases.append(Case(f"copy_{direction}_p{payload}_g{gap}_c{n}", op=op, cores=n, payload_bytes=payload, gap_bytes=gap))
    for pattern in range(1,8):
        for repeats in (1,4,16):
            cases.append(Case(f"gather_fixed{pattern}_r{repeats}", op=2, pattern=pattern, repeats=repeats, cores=min(8,max_cores)))
    for custom in ("none","all","even","random"):
        for elements in (1,31,63,64):
            cases.append(Case(f"gather_custom_{custom}_e{elements}", op=2, pattern=0, custom=custom, reduce=1, elements=elements, repeats=4, cores=min(8,max_cores)))
    cases += [Case("gather_strided", op=2, pattern=0, custom="random", repeats=4, src0_block_stride=2, src0_repeat_stride=16),
              Case("copy_delay_probe", cores=max_cores, delay_ticks=250)]
    return cases
