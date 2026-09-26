"""Host 回归；27 条预期名称来自先前 A5 profiler 报告，不由被测解析器生成。"""
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

from test_simt_target import elf
import audit_simt_opcodes as audit

# ProbeContexts [0x288, 0x3b0)，按原始顺序保留完整函数机器码。
REFERENCE = [
    ("5c069e02002e0006", "S2R"),
    ("9c000e12080a0606", "SHFI"),
    ("5d000e00000006040100000000000000", "IADD.i"),
    ("5c009e02000a0008", "S2R"),
    ("9d018e01d00118710100000000000000", "ISETP.i"),
    ("4001260148000000", "BRANCH"),
    ("dd040e000000080a0100000000100000", "IADD.i"),
    ("dd02060200080b0c0100000004000000", "FMUL.i"),
    ("9c0000e009000c0a", "LDG"),
    ("dd050e0000000a0a0100000001000000", "IADD.i"),
    ("9c000c1c05000c0a", "STG"),
    ("5d048e01d00114710100000000000000", "ISETP.i"),
    ("5c019e000000080a", "MOV"),
    ("4001260178000000", "BRANCH"),
    ("5c0416000000000c", "MOVI"),
    ("5c009e000000080a", "MOV"),
    ("9c009e000000080e", "MOV"),
    ("5d04060200080f100100000004000000", "FMUL.i"),
    ("9d020e0000000c0c0100000001000000", "IADD.i"),
    ("9c0000e00900100e", "LDG"),
    ("5c008e11d0150d70", "ISETP"),
    ("9d050600000e0a0a010000000d661900", "FMUL.i"),
    ("9d000e0000000a0a010000005ff36e3c", "IADD.i"),
    ("40012601b0ffffff", "BRANCH"),
    ("1c060ee602100908", "LEA"),
    ("9c00101c0400080a", "STS"),
    ("9c06a60600000000", "END"),
]


class OpcodeTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv("MSOPPROF_SOURCE"), "设置 MSOPPROF_SOURCE 以校验官方固定表")
    def test_profiler_reference(self):
        tables, hashes = audit.load_tables(Path(os.environ["MSOPPROF_SOURCE"]))
        self.assertEqual(len(hashes), 2)
        self.assertEqual(sum(len(bytes.fromhex(raw)) for raw, _ in REFERENCE), 0x128)
        for raw, expected in REFERENCE:
            with self.subTest(raw=raw):
                self.assertEqual(audit.classify(bytes.fromhex(raw), tables), [expected])

    def test_capture_scope_and_failures(self):
        for case in ("ok", "hash", "duplicate", "missing", "width", "symbol", "host", "address", "unknown", "ambiguous"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                capture = Path(folder)
                device = bytearray(elf({".text": b"\0" * 32}, machine=183 if case == "host" else 4137))
                struct.pack_into("<H", device, 16, 2)
                if case == "address":
                    offset = struct.unpack_from("<Q", device, 40)[0]
                    struct.pack_into("<Q", device, offset + 2 * 64 + 16, 0x1000)
                (capture / "device.o").write_bytes(device)
                (capture / "target-source.cpp").write_text("source")
                manifest = {"function": "Target", "device_sha256": hashlib.sha256(device).hexdigest(),
                            "source_sha256": hashlib.sha256(b"source").hexdigest()}
                if case == "hash":
                    manifest["device_sha256"] = "wrong"
                (capture / "manifest.json").write_text(json.dumps(manifest))
                symbol = "8 w F .text 10 Target() (.vector_simt_entry)\n"
                (capture / "symbols.log").write_text(symbol * (2 if case == "symbol" else 1))
                pcs = {"duplicate": [0, 8, 8, 16, 24], "missing": [0, 16, 24], "width": [0, 8, 12, 24]}
                (capture / "assembly.log").write_text("".join(f"{pc:x}: <not available>\n" for pc in pcs.get(case, [0, 8, 16, 24])))
                tables = {8: [((0,), {(0,): "FAKE"})], 16: []}
                if case == "unknown":
                    tables[8] = []
                if case == "ambiguous":
                    tables[8].append(((0,), {(0,): "OTHER"}))
                if case not in ("ok", "unknown", "ambiguous"):
                    with self.assertRaises(ValueError):
                        audit.audit(capture, tables)
                    continue
                report = audit.audit(capture, tables)
                self.assertEqual([i["pc"] for i in report["instructions"]], ["0x8", "0x10"])
                self.assertEqual(report["status"], "classified" if case == "ok" else "partial")
                self.assertFalse(report["hardware_executed"])
                for key in ("operands", "samples", "line_time"):
                    self.assertIsNone(report[key])
