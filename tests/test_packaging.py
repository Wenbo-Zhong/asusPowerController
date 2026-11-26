import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

PROJECT = Path(__file__).resolve().parent.parent

# macOS install lacks GNU -D; this fixture supplies only the file-copy command.
# The production stage.sh, find traversal and package imports run unchanged.
PORTABLE_INSTALL = """import os
from pathlib import Path
import shutil
import sys
args = sys.argv[1:]
mode = 0o755
directories = False
parents = False
paths = []
while args:
    arg = args.pop(0)
    if arg == '-d':
        directories = True
    elif arg.startswith('-Dm'):
        parents = True
        mode = int(arg[3:], 8)
    elif arg.startswith('-m') and arg != '-m':
        mode = int(arg[2:], 8)
    elif arg == '-m':
        mode = int(args.pop(0), 8)
    else:
        paths.append(Path(arg))
if directories:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)
else:
    destination = paths[-1]
    for source in paths[:-1]:
        target = destination / source.name if destination.is_dir() else destination
        if parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(mode)
"""


class PackagingTests(unittest.TestCase):
    def test_stage_contains_nested_modules_resources_and_no_caches(self):
        with TemporaryDirectory(prefix='asus stage ') as temp:
            root = Path(temp)
            source = root / 'source'
            source.mkdir()
            for directory in ('asus_power', 'scripts', 'packaging'):
                shutil.copytree(PROJECT / directory, source / directory)
            for name, text in {
                'asus_power/ui/assets/palette.json': '{"accent": "system"}',
                'asus_power/ui/.editor/state.json': 'ignored',
                'asus_power/ui/assets/.DS_Store': 'ignored',
                'asus_power/ui/__pycache__/extra.pyc': 'ignored',
                'asus_power/ui/extra.pyo': 'ignored',
            }.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            environment = os.environ.copy()
            if sys.platform == 'darwin':
                tools = root / 'tools'
                tools.mkdir()
                install = tools / 'install'
                install.write_text(f'#!{sys.executable}\n{PORTABLE_INSTALL}')
                install.chmod(0o755)
                environment['PATH'] = str(tools) + os.pathsep + environment['PATH']
            stage = root / 'payload'
            result = subprocess.run(['sh', 'packaging/stage.sh', str(stage)], cwd=source,
                                    env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            app = stage / 'usr/lib/asus-power-controller'
            expected = {str(path.relative_to(source)) for path in (source / 'asus_power').rglob('*')
                        if path.is_file() and not any(part.startswith('.') or part == '__pycache__'
                                                     for part in path.relative_to(source).parts)
                        and path.suffix not in ('.pyc', '.pyo')}
            actual = {str(path.relative_to(app)) for path in (app / 'asus_power').rglob('*')
                      if path.is_file()}
            self.assertEqual(actual, expected)
            self.assertIn('asus_power/ui/assets/palette.json', actual)
            self.assertEqual((app / 'asus_power/ui/controller.py').stat().st_mode & 0o777, 0o644)
            self.assertEqual((stage / 'usr/bin/asus-power-controller').stat().st_mode & 0o777, 0o755)
            probe = ('import sys; sys.path.insert(0,sys.argv[1]); '
                     'from asus_power.ui.drafts import CombinationDraft; '
                     'from asus_power.hardware.bus import BusClient; '
                     'from asus_power.backend import LinuxBackend')
            imported = subprocess.run([sys.executable, '-I', '-c', probe, str(app)], cwd=root,
                                      capture_output=True, text=True, timeout=10)
            self.assertEqual(imported.returncode, 0, imported.stdout + imported.stderr)
