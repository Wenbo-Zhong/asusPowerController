import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from asus_power.config import save

spec = importlib.util.spec_from_file_location(
    'desktop_launcher', Path(__file__).resolve().parents[1] / 'scripts/desktop_launcher.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class DesktopLauncherTests(unittest.TestCase):
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
                    patch('asus_power.config.os.chown') as chown:
                save({'enabled': True}, path)
                self.assertEqual(chown.call_count, 2)
                self.assertEqual(chown.call_args_list[0].args[0], path.with_suffix('.tmp'))
                self.assertEqual(chown.call_args_list[1].args[0], path.parent)
            self.assertIn('true', path.read_text())
