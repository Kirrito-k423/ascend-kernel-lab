# 原生 Qt Widgets + Agg；不引入 QtWebEngine，HTML 由系统浏览器打开。
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

root = Path(SPECPATH).parent
data = collect_data_files('akl', includes=['*.html', '*.js'])
for package in ('PySide6-Essentials', 'shiboken6', 'numpy', 'matplotlib', 'pyinstaller'):
    data += copy_metadata(package)
a = Analysis([str(root/'python/akl/desktop_entry.py')], pathex=[str(root/'python')],
             datas=data, excludes=['tkinter', 'PySide6.QtNetwork', 'PySide6.QtQml', 'PySide6.QtQuick'],
             hooksconfig={'matplotlib': {'backends': ['Agg', 'SVG']}})
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='AKL-Report', console=False,
          argv_emulation=False)
coll = COLLECT(exe, a.binaries, a.datas, name='AKL-Report')
if sys.platform == 'darwin':
    app = BUNDLE(coll, name='AKL Report.app', bundle_identifier='org.ascend-kernel-lab.report',
                 info_plist={'NSHighResolutionCapable': True})
