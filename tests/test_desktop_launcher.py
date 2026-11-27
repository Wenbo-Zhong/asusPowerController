import importlib.util
import os
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch
from asus_power.config import save
from asus_power.__main__ import gtk_fallback_command, main

spec = importlib.util.spec_from_file_location(
    'desktop_launcher', Path(__file__).resolve().parents[1] / 'scripts/desktop_launcher.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class DesktopLauncherTests(unittest.TestCase):
    def test_authorization_preserves_only_explicit_locale_variables(self):
        env = {'LANG': 'en_US.UTF-8', 'LC_MESSAGES': 'zh_TW.UTF-8',
               'LC_ALL': '', 'LANGUAGE': 'zh_TW:en', 'PYTHONPATH': '/untrusted'}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(launcher.shutil, 'which', return_value='/usr/bin/pkexec'):
            command = launcher.authorization_command()
        for key in ('LANG', 'LC_MESSAGES', 'LC_ALL', 'LANGUAGE'):
            self.assertIn(f'{key}={env[key]}', command)
        self.assertNotIn('PYTHONPATH=/untrusted', command)

    def test_desktop_launch_uses_current_user(self):
        with patch('sys.argv', ['desktop_launcher']), \
                patch('asus_power.config.load', return_value={}), \
                patch.object(launcher.os, 'chdir') as chdir, \
                patch.object(launcher.os, 'execv') as execute:
            launcher.main()
        chdir.assert_called_once_with(launcher.PROJECT)
        execute.assert_called_once_with('/usr/bin/python3', ['/usr/bin/python3', '-m', 'asus_power'])

    def test_opt_in_cancellation_falls_back_to_normal_launch(self):
        with patch('sys.argv', ['desktop_launcher']), \
                patch('asus_power.config.load', return_value={'request_admin_at_launch': True}), \
                patch.object(launcher.os, 'geteuid', return_value=1000), \
                patch.object(launcher.os, 'chdir'), \
                patch.object(launcher.os, 'execv') as execute, \
                patch.object(launcher, 'authorization_command', return_value=['pkexec', 'fixture']), \
                patch.object(launcher.subprocess, 'run') as authorize:
            authorize.return_value.returncode = 126
            launcher.main()
        authorize.assert_called_once_with(['pkexec', 'fixture'], check=False)
        execute.assert_called_once_with('/usr/bin/python3', ['/usr/bin/python3', '-m', 'asus_power'])

    def test_hidden_start_has_no_elevation_command(self):
        self.assertEqual(launcher.launch_command(start_hidden=True),
                         ['/usr/bin/python3', '-m', 'asus_power', '--start-hidden'])

    def test_elevated_settings_keep_user_ownership(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'app' / 'settings.json'
            with patch('asus_power.config.os.geteuid', return_value=0), \
                    patch.dict(os.environ, PKEXEC_UID=str(os.getuid())), \
                    patch('asus_power.config.os.fchown') as chown:
                descriptors = []
                chown.side_effect = lambda fd, uid, gid: descriptors.append(
                    (stat.S_ISREG(os.fstat(fd).st_mode), uid, gid))
                save({'enabled': True}, path)
                self.assertEqual(descriptors, [(True, os.getuid(), os.getgid())])
            self.assertIn('true', path.read_text())

    def test_gtk_fallback_exec_preserves_the_loaded_package_location(self):
        with patch('sys.argv', ['asus-power-controller', '--start-hidden']), \
                patch.dict(sys.modules, {'asus_power.ui.controller': None}), \
                patch('sys.executable', '/fixture/venv/bin/python'), \
                patch('os.path.isfile', return_value=True), \
                patch('os.execv', side_effect=SystemExit) as execute:
            with self.assertRaises(SystemExit):
                main()
        command = execute.call_args.args[1]
        self.assertEqual(command, gtk_fallback_command('/usr/bin/python3', ['--start-hidden']))
        self.assertEqual(command[-2], str(Path(__file__).resolve().parents[1]))

    def test_gtk_bootstrap_ignores_cwd_and_pythonpath_and_preserves_arguments(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'installed package/asus_power'
            package.mkdir(parents=True)
            (package / '__init__.py').write_text('')
            (package / '__main__.py').write_text(
                'import json,sys; print(json.dumps({"file":__file__,"argv":sys.argv[1:]}))')
            unrelated = root / 'cwd'
            unrelated.mkdir()
            (unrelated / 'asus_power.py').write_text('raise RuntimeError("untrusted cwd")')
            with patch('asus_power.__main__.__file__', str(package / '__main__.py')):
                command = gtk_fallback_command(sys.executable, ['--start-hidden'])
            result = subprocess.run(command, cwd=unrelated,
                                    env=dict(os.environ, PYTHONPATH=str(unrelated)),
                                    capture_output=True, text=True, timeout=5, check=True)
            data = json.loads(result.stdout)
            self.assertEqual(Path(data['file']), (package / '__main__.py').resolve())
            self.assertEqual(data['argv'], ['--start-hidden'])
