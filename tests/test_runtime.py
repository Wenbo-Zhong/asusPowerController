from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import subprocess
from asus_power.runtime import smooth_environment


class RuntimeTests(unittest.TestCase):
    def test_missing_runtime_does_not_modify_environment(self):
        with TemporaryDirectory() as path:
            self.assertIsNone(smooth_environment(path, {'TCL_LIBRARY': 'old'}))

    def test_local_runtime_preserves_imports_and_does_not_change_caller_env(self):
        with TemporaryDirectory() as path:
            root = Path(path)
            for relative in ('usr/lib/x86_64-linux-gnu/libtk8.6.so',
                             'usr/lib/python3.14/lib-dynload/_tkinter' + subprocess.check_output(
                                 ['/usr/bin/python3', '-E', '-S', '-c',
                                  'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))'], text=True).strip()):
                p = root / relative
                p.parent.mkdir(parents=True, exist_ok=True)
                p.touch()
            (root / 'usr/share/tcltk/tk8.6').mkdir(parents=True)
            old = {'TCL_LIBRARY': 'conda-tcl', 'PYTHONPATH': '/some/modules', 'LD_LIBRARY_PATH': '/some/libs'}
            python, env = smooth_environment(root, old)
            self.assertEqual(python, '/usr/bin/python3')
            self.assertNotIn('TCL_LIBRARY', env)
            self.assertTrue(env['PYTHONPATH'].endswith('/some/modules'))
            self.assertTrue(env['LD_LIBRARY_PATH'].endswith('/some/libs'))
            self.assertEqual(old['TCL_LIBRARY'], 'conda-tcl')
            self.assertEqual(env['ASUS_POWER_SMOOTH_TK'], '1')
