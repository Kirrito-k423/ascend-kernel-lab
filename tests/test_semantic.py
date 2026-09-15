"""CPU 合成记录验证；不作为 NPU 实测证据。"""
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from akl.semantic import MAGIC, decode_capture, event_map, path_hash, render


class Semantic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "kernel.cpp"
        self.path = ["big func", "sub func", "A part", "iteration"]
        self.source.write_text('DebugClock("big func", "sub func", "A part", "iteration");')
        self.mapping = event_map([self.source])
        self.key = path_hash(self.path)

    def capture(self, dropped=0):
        (self.root / "capture.json").write_text(json.dumps(dict(schema="akl.semantic.v1",
            capacity=4, blocks=2, rank=0, device=0, alignment="unverified")))
        rows = []
        for block in range(2):
            row = [MAGIC, 1, 4, dropped, block, 0, 0, 1]
            for seq in range(4):
                row += [self.key, 2**60 + 10*block + seq]
            rows += row
        (self.root / "trace.bin").write_bytes(struct.pack("<32Q", *rows))
        return rows

    def test_repetition_precision_and_counts(self):
        self.capture()
        meta, events, warnings = decode_capture(self.root, self.mapping)
        self.assertEqual([e["occurrence"] for e in events], [1, 2, 3, 4] * 2)
        self.assertEqual(events[0]["tick"], str(2**60))
        self.assertEqual(int(events[1]["tick"]) - int(events[0]["tick"]), 1)
        render(self.root, meta, events, warnings)
        counts = json.loads((self.root / "counts.json").read_text())
        self.assertEqual([s["count"] for s in counts["counts"]], [4, 4])
        self.assertEqual(len((self.root / "semantic.jsonl").read_text().splitlines()), 8)
        self.assertIn('data-level="3"', (self.root / "semantic.html").read_text())

    def test_overflow_is_visible(self):
        self.capture(dropped=7)
        meta, events, warnings = decode_capture(self.root, self.mapping)
        self.assertEqual(len(events), 8)
        self.assertEqual(len(warnings), 2)
        self.assertIn("dropped=7", warnings[0])

    def test_corruption_rejected(self):
        for index, value in ((0, 0), (1, 2), (2, 5), (4, 1), (7, 0), (8, 0), (11, 1)):
            with self.subTest(index=index):
                rows = self.capture()
                rows[index] = value
                (self.root / "trace.bin").write_bytes(struct.pack("<32Q", *rows))
                with self.assertRaises(ValueError):
                    decode_capture(self.root, self.mapping)
        (self.root / "trace.bin").write_bytes(b"short")
        with self.assertRaises(ValueError):
            decode_capture(self.root, self.mapping)

    def test_literal_grammar_and_insertion(self):
        original = dict(self.mapping)
        self.source.write_text('''// DebugClock("comment");
/* DebugClock("comment2"); */
const char* text = "DebugClock(ignored)";
#define DebugClock(...) AKL_DEBUG_CLOCK(clock_, __VA_ARGS__)
DebugClock("big func", "sub func", "A part", "iteration");
DebugClock("新增", "a, (b)", "quote\\\"");
AKL_DEBUG_CLOCK(clock_, "direct");''')
        mapping = event_map([self.source])
        self.assertEqual(len(mapping), 3)
        self.assertEqual(mapping[self.key], original[self.key])
        self.assertNotEqual(path_hash(["ab", "c"]), path_hash(["a", "bc"]))
        self.assertEqual(self.key, 2279751878)  # 与 CPU C++ 实际输出交叉核对。

    def test_invalid_literal_and_collision(self):
        for call in ('DebugClock(value);', 'DebugClock("");', 'DebugClock("a",);',
                     'DebugClock("a\\u0000b");', 'DebugClock("a" "b");'):
            self.source.write_text(call)
            with self.assertRaises(ValueError):
                event_map([self.source])
        self.source.write_text('DebugClock("a"); DebugClock("b");')
        with patch("akl.semantic.path_hash", return_value=1):
            with self.assertRaisesRegex(ValueError, "冲突"):
                event_map([self.source])

    def test_html_escaping_and_metadata_validation(self):
        self.capture()
        meta, events, warnings = decode_capture(self.root, {self.key: ['<script>alert("x")</script>']})
        render(self.root, meta, events, warnings)
        self.assertNotIn('<script>alert', (self.root / "semantic.html").read_text())
        meta["rank"] = "<script>"
        (self.root / "capture.json").write_text(json.dumps(meta))
        with self.assertRaises(ValueError):
            decode_capture(self.root, self.mapping)


if __name__ == "__main__":
    unittest.main()
