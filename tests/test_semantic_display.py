"""验证延迟创建图层仍完整保留所有 block 和 uint64 原始记录。"""
import json
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'python'))
from akl.semantic import render


class Display(unittest.TestCase):
    def test_lazy_payload_preserves_all_records_and_escaping(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta = dict(rank=0, device=0, blocks=64)
            events = [dict(block=b, subblock=0, sequence=i, occurrence=i+1,
                event_id=1, tick=str(2**60+b*10+i), path=['<script>unsafe</script>'])
                for b in range(64) for i in range(2)]
            render(root, meta, events, [])
            page = (root/'semantic.html').read_text()
            payload = json.loads(re.search(r'<script id="lane-data" type="application/json">(.*?)</script>', page, re.S)[1])
            self.assertEqual(len(payload), 64)
            self.assertIn(str(2**60+631), payload[63]['rows'])
            self.assertNotIn('<script>unsafe', page)
            self.assertIn('&lt;script&gt;unsafe', payload[63]['svg'])
            self.assertIn('value="0.001"', page)
            self.assertEqual(len(ET.parse(root/'semantic.svg').findall(".//{*}g[@class='lane']")), 64)
            original = (root/'semantic.jsonl').read_bytes()
            self.assertEqual(len(original.splitlines()), 128)
            render(root, meta, events, [], clock_mhz=2000)
            self.assertIn('value="0.0005"', (root/'semantic.html').read_text())
            self.assertEqual(original, (root/'semantic.jsonl').read_bytes())


if __name__ == '__main__':
    unittest.main()
