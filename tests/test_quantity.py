"""处理量协议与前向区间关联的 CPU 合成回归。"""
import json
import struct
import tempfile
import unittest
from pathlib import Path
from akl.semantic import MAGIC, decode_capture, event_map, render


class Quantity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.source = self.folder/'kernel.cpp'
        self.source.write_text('DebugClock("big", "copy", "begin");\n'
            'DebugClock("big", "copy", "end", (n + fn(2, 3)), "GB");')
        self.mapping = event_map([self.source])
        self.begin, self.end = self.mapping

    def capture(self, records):
        capacity = 4
        (self.folder/'capture.json').write_text(json.dumps(dict(schema='akl.semantic.v2',
            alignment='unverified', capacity=capacity, blocks=1, rank=0, device=0)))
        row = [MAGIC, 2, len(records), 0, 0, 0, 0, 1]
        for key, tick, amount, kind in records:
            row.extend([key, 2**60+tick, amount, kind])
        row.extend([0]*(8+4*capacity-len(row)))
        (self.folder/'trace.bin').write_bytes(struct.pack('<24Q',*row))
        return decode_capture(self.folder,self.mapping)

    def test_previous_interval_exact_count_and_fraction(self):
        fraction = struct.unpack('<I',struct.pack('<f',.5))[0]
        meta,events,warnings = self.capture([(self.begin,0,0,0),(self.end,250,1,1),
                                            (self.begin,400,0,0),(self.end,500,fraction,2)])
        render(self.folder,meta,events,warnings)
        self.assertEqual(events[1]['work']['elapsed_cycle'],'250')
        self.assertEqual(events[1]['work']['rate_per_us'],4)
        self.assertEqual(events[3]['work']['amount'],'0.5')
        self.assertEqual(events[3]['work']['rate_per_us'],5)
        trace=json.loads((self.folder/'trace.json').read_text())['traceEvents']
        intervals=[e for e in trace if e.get('args',{}).get('work')]
        self.assertEqual([(e['name'],e['dur']) for e in intervals],[('begin',.25),('begin',.1)])
        self.assertIn('data-work=',(self.folder/'semantic.html').read_text())
        self.assertIn('GB/us',(self.folder/'semantic.svg').read_text())
        render(self.folder,meta,events,warnings,500)
        self.assertEqual(events[1]['work']['rate_per_us'],2)
        exported=[json.loads(line) for line in (self.folder/'semantic.jsonl').read_text().splitlines()]
        self.assertEqual(exported[3]['work']['rate_per_us'],2.5)
        _,events,_=self.capture([(self.begin,0,0,0),(self.end,1,2**64-1,1)])
        self.assertEqual(events[1]['work']['amount'],str(2**64-1))

    def test_missing_previous_and_zero_duration(self):
        meta,events,warnings=self.capture([(self.end,0,4,1),(self.end,0,0,1)])
        render(self.folder,meta,events,warnings)
        self.assertIsNone(events[0]['work']['elapsed_cycle'])
        self.assertIsNone(events[0]['work']['rate_per_us'])
        self.assertEqual(events[1]['work']['elapsed_cycle'],'0')
        self.assertIsNone(events[1]['work']['rate_per_us'])

    def test_invalid_quantity_and_missing_unit(self):
        for kind,value in [(3,1),(2,0x7fc00000),(2,0xbf800000)]:
            with self.assertRaisesRegex(ValueError,'处理量'):
                self.capture([(self.end,1,value,kind)])
        for text in ['DebugClock("p", n);','DebugClock("p", n, "");','DebugClock("p", n, unit);']:
            self.source.write_text(text)
            with self.assertRaises(ValueError): event_map([self.source])


if __name__=='__main__':
    unittest.main()
