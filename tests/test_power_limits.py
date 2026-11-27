import json
import re
from asus_power.i18n import tr
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from asus_power.hardware.power_limits import PowerLimits
from asus_power.backend import LinuxBackend


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
        self.manager_running = False
        self.manager_automation = True
        device = self.root / 'bus/pci/devices/0000:01:00.0'
        (device / 'power').mkdir(parents=True)
        (device / 'power/runtime_status').write_text('active')
        (device / 'class').write_text('0x030200')
        (device / 'vendor').write_text('0x10de')
        (device / 'driver').symlink_to('/sys/bus/pci/drivers/nvidia')
        self.device = device
        self.api = PowerLimits(self.root, self.runner)

    def runner(self, args, **kwargs):
        if 'NameHasOwner' in args:
            return subprocess.CompletedProcess(args, 0, json.dumps({'data': [self.manager_running]}), '')
        if 'GetStatus' in args:
            data = [json.dumps({'automation_enabled': self.manager_automation})]
            return subprocess.CompletedProcess(args, 0, json.dumps({'data': data}), '')
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
        with self.assertRaisesRegex(ValueError, re.escape(tr('power.enable_first'))):
            self.api.apply('ppt_pl1_spl', 40)
        self.assertFalse(any('set-property' in call for call in self.calls))

    def test_apply_and_default_verify_sysfs(self):
        self.api.apply('ppt_pl1_spl', 40)
        self.assertEqual(self.api.read_attributes()['ppt_pl1_spl']['current'], 40)
        self.api.apply('ppt_pl1_spl', None, reset=True)
        self.assertEqual(self.api.read_attributes()['ppt_pl1_spl']['current'], 70)

    def test_cached_only_success_is_rejected(self):
        self.cache_only = True
        with self.assertRaisesRegex(RuntimeError, re.escape(tr('power.driver_mismatch'))):
            self.api.apply('ppt_pl1_spl', 40)
        self.assertEqual(self.api.read_attributes()['ppt_pl1_spl']['current'], 60)
        self.assertEqual(self.calls[-1][-1], '60')

    def test_gpu_manager_and_external_gpu_block_gpu_access(self):
        self.manager_running = True
        self.assertNotIn('nv_tgp', self.api.status()['attributes'])
        with self.assertRaisesRegex(RuntimeError, 'USB4 GPU Manager'):
            self.api.write('nv_tgp', 40)
        self.manager_automation = False
        self.assertIn('nv_tgp', self.api.status()['attributes'])
        self.assertEqual(tr('power.applied', label=tr('power.gpu_tgp'), value=40, unit='W'), self.api.apply('nv_tgp', 40))
        self.manager_automation = None
        self.assertNotIn('nv_tgp', self.api.status()['attributes'])
        self.manager_automation = False
        dock = self.device.with_name('0000:02:00.0')
        dock.mkdir()
        (dock / 'class').write_text('0x030000')
        (dock / 'removable').write_text('removable')
        self.assertNotIn('nv_tgp', self.api.read_attributes())
        self.assertEqual(self.api.apply('ppt_pl1_spl', 40), tr('power.applied', label=tr('power.cpu_sustained'), value=40, unit='W'))

    def test_firmware_deferral_after_gpu_preflight_is_reported_as_queued(self):
        self.defer = True
        self.assertEqual(tr('power.queued', label=tr('power.gpu_tgp'), value=40, unit='W'), self.api.apply('nv_tgp', 40))
        self.assertEqual(self.api.status()['attributes']['nv_tgp']['pending'], 40)
        self.assertEqual(self.api.read_attributes()['nv_tgp']['current'], 60)
        self.assertEqual(tr('power.queued', label=tr('power.gpu_tgp'), value=60, unit='W'), self.api.apply('nv_tgp', 60))

    def test_context_switch_does_not_restore_into_new_profile(self):
        self.switch_context = True
        with self.assertRaisesRegex(RuntimeError, re.escape(tr('power.context_changed'))):
            self.api.apply('ppt_pl1_spl', 40)
        writes = [call for call in self.calls if 'set-property' in call]
        self.assertEqual(len(writes), 1)

    def test_tuning_switch_is_verified(self):
        self.api.set_enabled(False)
        self.assertFalse(self.api.status()['enabled'])
        self.api.set_enabled(True)
        self.assertTrue(self.api.status()['enabled'])

    def supply(self, name, **values):
        directory = self.root / 'class/power_supply' / name
        directory.mkdir(parents=True, exist_ok=True)
        for key, value in values.items():
            (directory / key).write_text(str(value))
        return directory

    def usb_transition(self, combined):
        battery = self.supply('BAT0', type='Battery', status='Charging')
        self.supply('USB', type='USB', online=1)
        failed = False

        def transition(args, **kwargs):
            nonlocal failed
            if '--help' in args:
                return subprocess.CompletedProcess(args, 0, 'profile battery', '')
            result = self.runner(args, **kwargs)
            if 'set-property' in args and args[-3] == 'CurrentValue' and not failed:
                failed = True
                (battery / 'status').write_text('Discharging')
                (self.attributes / 'ppt_pl1_spl/current_value').write_text('80')
                return subprocess.CompletedProcess(args, 1, '', 'write failed during transition')
            return result

        backend = LinuxBackend(self.root, transition, '/simulated/asusctl')
        self.api.bus.runner = transition
        expected = (tr('combination.aborted_context', error=tr('power.context_changed'))
                    if combined else tr('power.context_changed'))
        with self.assertRaisesRegex(RuntimeError, re.escape(expected)):
            if combined:
                backend.apply_combination(
                    'balanced', 'ac', {'power': {'ppt_pl1_spl': 40}, 'fans': {}})
            else:
                self.api.apply('ppt_pl1_spl', 40)
        writes = [call for call in self.calls if 'set-property' in call]
        self.assertEqual([call[-1] for call in writes], ['40'])
        self.assertEqual(self.api.read_attributes()['ppt_pl1_spl']['current'], 80)
        self.assertIs(self.api.context()['ac'], False)
        self.assertEqual(self.api.context(), backend.combination_context())

    def test_usb_status_transition_aborts_direct_power_without_old_rollback(self):
        self.usb_transition(combined=False)

    def test_usb_status_transition_aborts_combination_without_old_rollback(self):
        self.usb_transition(combined=True)

    def test_peripheral_online_changes_do_not_abort_system_power_transaction(self):
        self.supply('BAT0', type='Battery', status='Charging')
        peripheral = self.supply('mouse', type='USB', scope='Device', online=0)
        original = self.runner

        def change_peripheral(args, **kwargs):
            result = original(args, **kwargs)
            if 'set-property' in args:
                (peripheral / 'online').write_text('1')
            return result

        self.api.bus.runner = change_peripheral
        self.assertEqual(tr('power.applied', label=tr('power.cpu_sustained'), value=40, unit='W'), self.api.apply('ppt_pl1_spl', 40))
        self.assertEqual(self.api.read_attributes()['ppt_pl1_spl']['current'], 40)

    def test_context_uses_threshold_capable_system_battery_and_mains_priority(self):
        self.supply('BAT0', type='Battery', status='Charging')
        selected = self.supply('BAT1', type='Battery', status='Discharging',
                               charge_control_end_threshold=80)
        self.supply('mouse', type='Battery', scope='Device', status='Charging')
        self.supply('USB', type='USB', online=1)
        self.assertIs(self.api.context()['ac'], False)
        self.supply('ACAD', type='Mains', online=1)
        self.assertIs(self.api.context()['ac'], True)
        self.assertEqual(selected.name, 'BAT1')
