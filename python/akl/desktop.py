"""原生拖放入口；绘图子进程与窗口分离，解析时仍可移动窗口。"""
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QProcess, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QMainWindow, QProgressBar, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)


class ReportWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('AKL Report · 离线实验报告')
        self.resize(720, 570)
        self.setAcceptDrops(True)
        self.input = None
        self.temporary = None
        self.process = QProcess(self)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.failed)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.read_progress)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(30, 24, 30, 24)
        title = QLabel('把实验压缩包拖到这里')
        title.setStyleSheet('font-size:26px;font-weight:600;padding:28px;background:#e8efff;color:#183047;border-radius:12px')
        title.setWordWrap(True)
        layout.addWidget(title)
        intro = QLabel('支持 ZIP 或 latency CSV · 本地解析 · 不上传数据\n自动生成 HTML、PNG、SVG 和 Chrome Trace JSON，无需安装 Python 或指定 C++。')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.choose = QPushButton('选择实验包 / CSV…')
        self.choose.clicked.connect(self.choose_input)
        layout.addWidget(self.choose)
        self.filename = QLabel('尚未选择文件')
        self.filename.setWordWrap(True)
        layout.addWidget(self.filename)
        layout.addWidget(QLabel('结果目录（必须为新目录）'))
        row = QHBoxLayout()
        self.output = QLineEdit()
        row.addWidget(self.output)
        self.browse = QPushButton('选择父目录…')
        self.browse.clicked.connect(self.choose_output)
        row.addWidget(self.browse)
        layout.addLayout(row)
        options = QHBoxLayout()
        options.addWidget(QLabel('解析进程'))
        self.jobs = QSpinBox()
        self.jobs.setRange(1, 64)
        self.jobs.setValue(min(4, os.cpu_count() or 1))
        options.addWidget(self.jobs)
        options.addWidget(QLabel('Trace 1 cycle ='))
        self.cycle = QLineEdit()
        self.cycle.setPlaceholderText('0.001（显示换算）')
        options.addWidget(self.cycle)
        options.addWidget(QLabel('µs'))
        layout.addLayout(options)
        self.start = QPushButton('生成报告')
        self.start.setEnabled(False)
        self.start.clicked.connect(self.run)
        layout.addWidget(self.start)
        self.bar = QProgressBar()
        self.bar.setValue(0)
        layout.addWidget(self.bar)
        self.status = QLabel('新实验包自带名称映射；旧包没有映射时显示 event ID。\n输入文件始终保留，输出目录可直接复制给同事。')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.open_report = QPushButton('打开 HTML 报告')
        self.open_folder = QPushButton('打开结果目录')
        for button in (self.open_report, self.open_folder):
            button.setEnabled(False)
            buttons.addWidget(button)
        self.open_report.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result/'index.html'))))
        self.open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result))))
        layout.addLayout(buttons)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if self.process.state() == QProcess.NotRunning and len(urls) == 1 and urls[0].isLocalFile():
            if Path(urls[0].toLocalFile()).suffix.lower() in ('.zip', '.csv'):
                event.acceptProposedAction()

    def dropEvent(self, event):
        self.select(Path(event.mimeData().urls()[0].toLocalFile()))
        event.acceptProposedAction()

    def choose_input(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择实验包', '', '实验数据 (*.zip *.csv)')
        if path:
            self.select(Path(path))

    def select(self, path):
        self.input = path.resolve()
        self.filename.setText(self.input.name)
        target = self.input.with_name(self.input.stem+'-result')
        index = 1
        while target.exists():
            target = self.input.with_name(f'{self.input.stem}-result-{index}')
            index += 1
        self.output.setText(str(target))
        self.start.setEnabled(True)
        self.open_report.setEnabled(False)
        self.open_folder.setEnabled(False)

    def choose_output(self):
        directory = QFileDialog.getExistingDirectory(self, '选择结果父目录')
        if directory:
            self.output.setText(str(Path(directory)/((self.input.stem if self.input else 'experiment')+'-result')))

    def run(self):
        self.result = Path(self.output.text()).expanduser().resolve()
        if not self.output.text().strip() or self.result.exists():
            self.status.setText('请选择一个尚不存在的结果目录。')
            return
        args = ['--render', str(self.input), '--output', str(self.result), '--jobs', str(self.jobs.value())]
        if self.cycle.text().strip():
            try:
                import math
                value = float(self.cycle.text())
                if not math.isfinite(value) or value <= 0:
                    raise ValueError()
                args += ['--clock-mhz', str(1/value)]
            except ValueError:
                self.status.setText('cycle 换算值必须为有限正数。')
                return
        self.temporary = tempfile.TemporaryDirectory(prefix='akl-desktop-')
        self.log = Path(self.temporary.name)/'progress.log'
        args += ['--log-file', str(self.log)]
        if not getattr(sys, 'frozen', False):
            args = [str(Path(__file__).with_name('desktop_entry.py')), *args]
        self.set_busy(True)
        self.status.setText('正在启动解析器…')
        self.process.start(sys.executable, args)
        self.timer.start(500)

    def set_busy(self, busy):
        for control in (self.choose, self.browse, self.start, self.output, self.jobs, self.cycle):
            control.setEnabled(not busy)
        self.bar.setRange(0, 0 if busy else 100)
        self.bar.setValue(0)
        self.open_report.setEnabled(False)
        self.open_folder.setEnabled(False)

    def read_progress(self):
        if self.log.exists():
            with self.log.open('rb') as stream:
                stream.seek(max(0, self.log.stat().st_size-2000))
                lines = stream.read().decode('utf-8', errors='replace').strip().splitlines()
            if lines:
                self.status.setText('\n'.join(lines[-3:]))

    def finished(self, code, _status):
        self.timer.stop()
        self.read_progress()
        self.set_busy(False)
        if code == 0 and (self.result/'index.html').is_file():
            self.bar.setValue(100)
            self.status.setText('报告已生成。点击下方按钮查看，或复制整个结果目录分享。')
            self.open_report.setEnabled(True)
            self.open_folder.setEnabled(True)
        else:
            self.status.setText('解析失败；输入文件保留。\n'+self.status.text())
        if self.temporary:
            self.temporary.cleanup()
            self.temporary = None

    def failed(self, error):
        if error == QProcess.FailedToStart:
            self.timer.stop()
            self.set_busy(False)
            self.status.setText('无法启动解析器：'+self.process.errorString())
            if self.temporary:
                self.temporary.cleanup()
                self.temporary = None

    def closeEvent(self, event):
        if self.process.state() != QProcess.NotRunning:
            self.status.setText('解析正在进行，请完成后关闭窗口。')
            event.ignore()
        else:
            super().closeEvent(event)


def main():
    application = QApplication(sys.argv[:1])
    window = ReportWindow()
    window.show()
    return application.exec()
