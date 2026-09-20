"""CPU ACL stub; no NPU execution."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

class Retention(unittest.TestCase):
    def test_export_retention(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            binary = str(Path(temporary)/'retention')
            subprocess.run([os.getenv('CXX','c++'),'-std=c++17','-O2','-pthread','-Wall','-Wextra','-Werror',
                '-I'+str(root/'include'),'-I'+str(root/'tests/retention_stubs'),
                str(root/'tests/keep_last.cpp'),'-o',binary],check=True)
            subprocess.run([binary,str(Path(temporary)/'captures')],check=True)

if __name__=='__main__': unittest.main()
