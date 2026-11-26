import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from asus_power.config import load, save


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'app/settings.json'
        self.path.parent.mkdir()

    def test_fixed_temp_symlink_does_not_overwrite_or_chown_target(self):
        victim = self.root / 'victim'
        victim.write_text('original')
        self.path.with_suffix('.tmp').symlink_to(victim)
        with patch.dict(os.environ, PKEXEC_UID=str(os.getuid())), \
                patch('asus_power.config.os.geteuid', return_value=0), \
                patch('asus_power.config.os.chown') as path_chown:
            save({'enabled': True}, self.path)
        self.assertEqual(victim.read_text(), 'original')
        self.assertFalse(self.path.is_symlink())
        self.assertEqual(load(self.path), {'enabled': True})
        path_chown.assert_not_called()
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_destination_symlink_is_replaced_without_following(self):
        victim = self.root / 'victim'
        victim.write_text('original')
        self.path.symlink_to(victim)
        save({'enabled': True}, self.path)
        self.assertEqual(victim.read_text(), 'original')
        self.assertFalse(self.path.is_symlink())
        self.assertEqual(load(self.path), {'enabled': True})

    def test_exclusive_temp_collision_preserves_both_files(self):
        self.path.write_text('original settings')
        victim = self.root / 'victim'
        victim.write_text('original target')
        collision = self.path.parent / '.settings.json.collision.tmp'
        collision.symlink_to(victim)
        with patch('asus_power.config.secrets.token_hex', return_value='collision'):
            with self.assertRaises(FileExistsError):
                save({'enabled': True}, self.path)
        self.assertEqual(self.path.read_text(), 'original settings')
        self.assertEqual(victim.read_text(), 'original target')
        self.assertTrue(collision.is_symlink())

    def test_failed_replace_preserves_settings_and_removes_own_temp(self):
        self.path.write_text('original')
        with patch('asus_power.config.os.replace', side_effect=OSError('replace denied')):
            with self.assertRaisesRegex(OSError, 'replace denied'):
                save({'enabled': True}, self.path)
        self.assertEqual(self.path.read_text(), 'original')
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_app_directory_symlink_is_rejected(self):
        linked = self.root / 'linked'
        linked.symlink_to(self.path.parent, target_is_directory=True)
        with self.assertRaises(OSError):
            save({'enabled': True}, linked / 'settings.json')
        self.assertFalse(self.path.exists())

    def test_elevated_save_rejects_a_different_directory_owner(self):
        user = SimpleNamespace(pw_uid=os.getuid() + 1, pw_gid=os.getgid())
        with patch.dict(os.environ, PKEXEC_UID=str(user.pw_uid)), \
                patch('asus_power.config.os.geteuid', return_value=0), \
                patch('asus_power.config.pwd.getpwuid', return_value=user), \
                patch('asus_power.config.os.fchown') as chown:
            with self.assertRaisesRegex(PermissionError, '配置目录'):
                save({'enabled': True}, self.path)
        chown.assert_not_called()
        self.assertEqual(list(self.path.parent.iterdir()), [])
