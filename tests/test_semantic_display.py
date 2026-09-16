"""验证延迟创建图层仍完整保留所有 block 和 uint64 原始记录。"""
import colorsys
import json
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'python'))
from akl.semantic import render, segment_color


class Display(unittest.TestCase):
    def test_palette_contrast_and_duration_order(self):
        def luminance(color):
            rgb = [int(color[i:i+2], 16)/255 for i in (1, 3, 5)]
            linear = [c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4 for c in rgb]
            return sum(c*w for c, w in zip(linear, (.2126, .7152, .0722)))
        previous_max = 0
        for duration in (0, 1, 100, 10000):
            colors = [segment_color(i, level, duration, 10000) for level in range(4) for i in range(36)]
            values = [luminance(color) for color in colors]
            self.assertGreater(min(values), previous_max)
            previous_max = max(values)
            self.assertGreater(min((value+.05)/(luminance('#111111')+.05) for value in values), 5)
        hues = [colorsys.rgb_to_hls(*(int(segment_color(i, 0, 1, 10000)[j:j+2], 16)/255
                for j in (1, 3, 5)))[0]*360 for i in range(36)]
        self.assertGreater(min(min(abs(a-b), 360-abs(a-b)) for a, b in zip(hues, hues[1:])), 130)

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
            fills = [r.get('fill') for r in ET.parse(root/'semantic.svg').findall('.//{*}g[@data-start]/{*}rect')]
            original = (root/'semantic.jsonl').read_bytes()
            self.assertEqual(len(original.splitlines()), 128)
            render(root, meta, events, [], clock_mhz=2000)
            self.assertIn('value="0.0005"', (root/'semantic.html').read_text())
            self.assertEqual(original, (root/'semantic.jsonl').read_bytes())
            render(root, meta, events, [], cycle_range=(1, 2))
            self.assertEqual(fills, [r.get('fill') for r in ET.parse(root/'semantic.svg').findall('.//{*}g[@data-start]/{*}rect')])


if __name__ == '__main__':
    unittest.main()
