import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from asus_power.power_limits import PowerLimits


class PowerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        profile = self.root / 'firmware/acpi/platform_profile'
        profile.parent.mkdir(parents=True)
        profile.write_text('balanced')
        self.attributes = self.root / 'class/firmware-attributes/asus-armoury/attributes'
        for name in ('ppt_pl1_spl', 'nv_tgp'):
            directory = self.attributes / name
            directory.mkdir(parents=True)
            for key, value in {'min_value': 15, 'max_value': 80, 'current_value': 60,
                               'default_value': 70, 'scalar_increment': 5}.items():
                (directory / key).write_text(str(value))
        self.enabled = True
        self.queue = {}
        self.defer = False
        self.cache_only = False
        self.switch_context = False
        self.calls = []
        self.api = PowerLimits(self.root, self.runner)

    def runner(self, args, **kwargs):
        self.calls.append(args)
        if 'get-property' in args:
            value = self.enabled if args[-1] == 'EnablePptGroup' else self.queue.get(args[-3].split('/')[-1], -1)
            return subprocess.CompletedProcess(args, 0, json.dumps({'data': value}), '')
        if args[-3] == 'EnablePptGroup':
            self.enabled = args[-1] == 'true'
        else:
            name = args[-5].split('/')[-1]
            value = int(args[-1])
            if self.defer:
                self.queue[name] = value
            elif not self.cache_only:
                (self.attributes / name / 'current_value').write_text(str(value))
            if self.switch_context:
                (self.root / 'firmware/acpi/platform_profile').write_text('performance')
        return subprocess.CompletedProcess(args, 0, '', '')

    def test_actual_values_ranges_and_defaults_are_discovered(self):
        data = self.api.status()
        self.assertTrue(data['supported'])
        self.assertEqual(data['attributes']['ppt_pl1_spl']['step'], 5)
        self.assertEqual(data['attributes']['ppt_pl1_spl']['current'], 60)

    def test_rejects_invalid_values_before_write(self):
        for value in (True, '40', 14, 81, 42):
            with self.assertRaises(ValueError):
                self.api.apply('ppt_pl1_spl', value)
        with self.assertRaises(ValueError):
            self.api.apply('../current_value', 40)
        self.assertFalse(any('set-property' in call for call in self.calls))

    def test_requires_explicit_tuning_enabled(self):
        self.enabled = False
        with self.assertRaisesRegex(ValueError, '启用'):
            self.api.apply('ppt_pl1_spl', 40)
        self.assertFalse(any('set-property' in call for call in self.calls))

    def test_apply_and_default_verify_sysfs(self):
        self.api.apply('ppt_pl1_spl', 40)
        self.assertEqual(self.api.read_attributes()['ppt_pl1_spl']['current'], 40)
        self.api.apply('ppt_pl1_spl', None, reset=True)
        self.assertEqual(self.api.read_attributes()['ppt_pl1_spl']['current'], 70)

    def test_cached_only_success_is_rejected(self):
        self.cache_only = True
        with self.assertRaisesRegex(RuntimeError, '驱动实际'):
            self.api.apply('ppt_pl1_spl', 40)
        self.assertEqual(self.api.read_attributes()['ppt_pl1_spl']['current'], 60)
        self.assertEqual(self.calls[-1][-1], '60')

    def test_sleeping_gpu_setting_is_reported_as_queued(self):
        self.defer = True
        self.assertIn('已排队', self.api.apply('nv_tgp', 40))
        self.assertEqual(self.api.status()['attributes']['nv_tgp']['pending'], 40)
        self.assertEqual(self.api.read_attributes()['nv_tgp']['current'], 60)
        self.assertIn('已排队', self.api.apply('nv_tgp', 60))

    def test_context_switch_does_not_restore_into_new_profile(self):
        self.switch_context = True
        with self.assertRaisesRegex(RuntimeError, '切换'):
            self.api.apply('ppt_pl1_spl', 40)
        writes = [call for call in self.calls if 'set-property' in call]
        self.assertEqual(len(writes), 1)

    def test_tuning_switch_is_verified(self):
        self.api.set_enabled(False)
        self.assertFalse(self.api.status()['enabled'])
        self.api.set_enabled(True)
        self.assertTrue(self.api.status()['enabled'])
