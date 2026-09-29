"""合成 ABI / CSV 的便携包回放，不代表 NPU 实测。"""
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile

from akl.report_bundle import convert, extract, pack, save_event_map
from akl.semantic import MAGIC, decode_capture, load_event_map, path_hash


def fixture(root):
    source = root/'kernel.cpp'
    source.write_text('DebugClock("dispatch", "send"); DebugClock("done");', encoding='utf-8')
    raw = root/'raw'
    for rank in range(2):
        target = raw/f'rank{rank}-pid42-launch1'
        target.mkdir(parents=True)
        (target/'capture.json').write_text(json.dumps(dict(schema='akl.semantic.v1',
            alignment='unverified', capacity=4, blocks=2, rank=rank, device=rank)))
        words = []
        for block in range(2):
            words += [MAGIC, 1, 2, 0, block, 0, 0, 1,
                      path_hash(['dispatch', 'send']), 2**60, path_hash(['done']), 2**60+150+block*50, 0, 0, 0, 0]
        (target/'trace.bin').write_bytes(struct.pack(f'<{len(words)}Q', *words))
    latency = root/'dispatch_latency.csv'
    latency.write_text('rank,iteration,elapsed_us,is_warmup,processed_bytes,sent_bytes,work_kind\n'
        + ''.join(f'{r},{i},{100+r*3+i},{int(i==0)},4096,8192,tokens\n' for r in range(2) for i in range(3)))
    return raw, latency, source


class Portable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.raw, self.csv, self.source = fixture(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pack_and_parallel_report(self):
        archive = pack(self.raw, self.root/'实验.zip', self.csv, [self.source])
        before = hashlib.sha256(archive.read_bytes()).digest()
        self.source.unlink()
        report = convert(archive, self.root/'报告', jobs=2)
        self.assertTrue(report.is_file())
        self.assertEqual(before, hashlib.sha256(archive.read_bytes()).digest())
        self.assertEqual(len(list(report.parent.rglob('semantic.html'))), 2)
        self.assertGreaterEqual(len(list(report.parent.rglob('*.png'))), 7)
        self.assertFalse(list(report.parent.rglob('trace.bin')))
        html = next(report.parent.rglob('semantic.html')).read_text(encoding='utf-8')
        self.assertIn('dispatch', html)
        self.assertNotIn('name unavailable', html)
        self.assertIn('1152921504606846976', next(report.parent.rglob('trace.json')).read_text(encoding='utf-8'))
        with self.assertRaisesRegex(ValueError, '已存在'):
            convert(archive, report.parent)

    def test_legacy_names_and_csv_only(self):
        archive = pack(self.raw, self.root/'old.zip')
        index = convert(archive, self.root/'legacy', jobs=1)
        self.assertIn('缺少 event_map', index.read_text(encoding='utf-8'))
        self.assertIn('name unavailable', next(index.parent.rglob('semantic.html')).read_text(encoding='utf-8'))
        self.assertTrue(convert(self.csv, self.root/'csv-only').exists())

    def test_missing_metadata_and_unknown_names_fail_atomically(self):
        archive = self.root/'bad.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('trace.bin', b'raw')
        with self.assertRaisesRegex(ValueError, 'capture.json'):
            convert(archive, self.root/'absent')
        self.assertFalse((self.root/'absent').exists())
        mapping = self.root/'event_map.json'
        save_event_map([self.source], mapping)
        loaded = load_event_map(mapping)
        del loaded[path_hash(['done'])]
        with self.assertRaisesRegex(ValueError, '事件映射不匹配'):
            decode_capture(next(self.raw.iterdir()), loaded)

    def test_zip_safety(self):
        for name in ('../trace.bin', '/capture.json', 'C:/trace.bin', 'a\\trace.bin', 'NUL.csv', 'x./trace.bin'):
            with self.subTest(name=name):
                archive = self.root/'bad.zip'
                with zipfile.ZipFile(archive, 'w') as z:
                    info = zipfile.ZipInfo()
                    info.filename = name  # 绕过 Windows 写入器的自动斜杠替换，模拟真实原始 ZIP。
                    z.writestr(info, b'bad')
                with self.assertRaises(ValueError):
                    extract(archive, self.root)
        info = zipfile.ZipInfo('trace.bin')
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr(info, '../outside')
        with self.assertRaises(ValueError):
            extract(archive, self.root)
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('trace.bin', b'1234')
        with self.assertRaises(ValueError):
            extract(archive, self.root, max_bytes=3)

    def test_desktop_drop(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtCore import QEventLoop, QMimeData, QPointF, QTimer, Qt, QUrl
        from PySide6.QtGui import QDropEvent
        from PySide6.QtWidgets import QApplication
        from akl.desktop import ReportWindow
        app = QApplication.instance() or QApplication([])
        window = ReportWindow()
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(self.csv))])
        event = QDropEvent(QPointF(10, 10), Qt.CopyAction, data, Qt.LeftButton, Qt.NoModifier)
        window.dropEvent(event)
        self.assertEqual(window.input, self.csv.resolve())
        self.assertTrue(window.start.isEnabled())
        loop = QEventLoop()
        window.process.finished.connect(loop.quit)
        window.run()
        QTimer.singleShot(30_000, loop.quit)
        loop.exec()
        self.assertTrue(window.open_report.isEnabled(), window.status.text())
        self.assertTrue((window.result/'index.html').is_file())
        window.close()

    @unittest.skipUnless(os.environ.get('AKL_APP_EXE'), 'set AKL_APP_EXE to test packaged executable')
    def test_frozen_parallel_report(self):
        archive = pack(self.raw, self.root/'portable.zip', self.csv, [self.source])
        output = self.root/'frozen'
        log = self.root/'frozen.log'
        environment = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'VIRTUAL_ENV')}
        result = subprocess.run([os.environ['AKL_APP_EXE'], '--render', str(archive), '--output', str(output),
            '--jobs', '2', '--log-file', str(log)], timeout=180, env=environment, cwd=self.root)
        self.assertEqual(result.returncode, 0, log.read_text(encoding='utf-8') if log.exists() else 'no log')
        self.assertEqual(len(list(output.rglob('semantic.html'))), 2)
        self.assertTrue((output/'index.html').exists())


if __name__ == '__main__':
    unittest.main()
