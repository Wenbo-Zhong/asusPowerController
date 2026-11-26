import json
from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import unittest
from unittest.mock import patch
from asus_power.backend import LinuxBackend, HardwareError
from asus_power.config import Policy, load, save

class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.calls = []
        self.put('class/power_supply/BAT1/type', 'Battery')
        self.put('class/power_supply/BAT1/capacity', '77')
        self.put('class/power_supply/BAT1/charge_control_end_threshold', '80')
        self.put('class/power_supply/ACAD/type', 'Mains')
        self.put('class/power_supply/ACAD/online', '0')
        self.put('class/power_supply/USB/type', 'USB')
        self.put('class/power_supply/USB/online', '1')
        self.put('firmware/acpi/platform_profile_choices', 'quiet balanced performance')
        self.put('firmware/acpi/platform_profile', 'quiet')
        self.backend = LinuxBackend(self.root, self.run_command, '/usr/bin/asusctl')

    def tearDown(self):
        self.temp.cleanup()

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def run_command(self, args, **kwargs):
        self.calls.append(args)
        return subprocess.CompletedProcess(args, 0, 'profile battery' if '--help' in args else '', '')

    def test_discover_bat1_and_usb_power(self):
        self.assertEqual(self.backend.snapshot()['battery'], 77)
        self.assertTrue(self.backend.snapshot()['ac'])
        self.assertTrue(self.backend.capabilities()['charge_limit'])

    def test_usb_pd_online_does_not_override_battery_discharge(self):
        self.put('class/power_supply/BAT1/status', 'Discharging')
        self.assertIs(self.backend.snapshot()['ac'], False)
        self.assertIs(self.backend.combination_context()['ac'], False)
        self.put('class/power_supply/BAT1/status', 'Charging')
        self.assertIs(self.backend.combination_context()['ac'], True)
        self.put('class/power_supply/BAT1/status', 'Not charging')
        self.assertIs(self.backend.combination_context()['ac'], True)
        self.put('class/power_supply/USB/scope', 'Device')
        self.assertIs(self.backend.combination_context()['ac'], False)

    def test_mains_is_authoritative_when_battery_discharges(self):
        self.put('class/power_supply/ACAD/online', '1')
        self.put('class/power_supply/BAT1/status', 'Discharging')
        self.assertIs(self.backend.combination_context()['ac'], True)

    def test_missing_and_malformed_telemetry(self):
        self.put('class/power_supply/BAT1/capacity', 'bad')
        self.assertIsNone(self.backend.snapshot()['battery'])
        self.assertIsNone(self.backend.snapshot()['battery_watts'])

    def test_invalid_writes_never_execute(self):
        count = len(self.calls)
        for value in [0, 101, '80', True]:
            with self.assertRaises(ValueError):
                self.backend.set_limit(value)
        with self.assertRaises(ValueError):
            self.backend.set_profile('balanced; reboot')
        self.assertEqual(count, len(self.calls))

    def test_write_must_be_read_back(self):
        with self.assertRaises(HardwareError):
            self.backend.set_profile('balanced')
        with self.assertRaises(HardwareError):
            self.backend.set_limit(90)
        self.assertEqual(self.calls[-1], ['/usr/bin/asusctl', 'battery', 'limit', '90'])

    def test_process_failure_propagates(self):
        self.backend.runner = lambda *a, **kw: subprocess.CompletedProcess(a, 1, '', 'D-Bus unavailable')
        with self.assertRaisesRegex(HardwareError, 'D-Bus unavailable'):
            self.backend.set_limit(80)

    def test_power_policy_transitions_and_unknown(self):
        policy = Policy()
        settings = {'enabled': True, 'ac': 'balanced', 'battery': 'quiet'}
        self.assertIsNone(policy.target(True, settings))
        self.assertIsNone(policy.target(True, settings))
        self.assertEqual(policy.target(False, settings), 'quiet')
        self.assertIsNone(policy.target(False, settings))
        self.assertIsNone(policy.target(None, settings))
        self.assertIsNone(policy.target(False, settings))
        self.assertIsNone(policy.target(True, {'enabled': False}))
        self.assertIsNone(policy.target(True, settings))
        self.assertEqual(policy.target(False, settings), 'quiet')

    def test_queued_power_switch_preserves_a_newer_profile_or_power_source(self):
        count = len(self.calls)
        self.put('firmware/acpi/platform_profile', 'performance')
        self.backend.switch_profile_on_power_change('balanced', 'quiet', True)
        self.backend.switch_profile_on_power_change('balanced', 'performance', False)
        self.assertEqual(len(self.calls), count)
        with self.assertRaises(HardwareError):
            self.backend.switch_profile_on_power_change('balanced', 'performance', True)
        self.assertEqual(self.calls[-1], ['/usr/bin/asusctl', 'profile', 'set', 'balanced'])

    def test_corrupt_config_and_roundtrip(self):
        path = self.root / 'config/settings.json'
        save({'enabled': False}, path)
        self.assertEqual(load(path), {'enabled': False})
        path.write_text('[]')
        self.assertEqual(load(path), {})
        path.write_text('{')
        self.assertEqual(load(path), {})

    def gpu_sensors(self, vendor, status, external=False):
        device = self.root / 'bus/pci/devices/0000:03:00.0'
        self.put(str(device.relative_to(self.root) / 'class'), '0x030000')
        self.put(str(device.relative_to(self.root) / 'vendor'), vendor)
        self.put(str(device.relative_to(self.root) / 'power/runtime_status'), status)
        self.put(str(device.relative_to(self.root) / 'removable'),
                 'removable' if external else 'fixed')
        sensors = device / 'hwmon/hwmon3'
        self.put(str(sensors.relative_to(self.root) / 'name'), 'gpu')
        self.put(str(sensors.relative_to(self.root) / 'temp1_input'), '50000')
        self.put(str(sensors.relative_to(self.root) / 'fan1_input'), '1200')
        link = self.root / 'class/hwmon/hwmon3'
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.exists():
            link.symlink_to(sensors, target_is_directory=True)
        self.backend.hwmon = [link]
        sensor_root = sensors.resolve()
        reads = []
        original = Path.read_text

        def track(path, *args, **kwargs):
            if sensor_root in path.resolve().parents:
                reads.append(path.name)
            return original(path, *args, **kwargs)

        with patch.object(Path, 'read_text', track), \
                patch.object(self.backend, 'power_limits', return_value={}), \
                patch.object(self.backend, 'igpu_clock', return_value={}), \
                patch.object(self.backend, 'cpu_epp_status', return_value={}):
            state = self.backend.snapshot()
        return state['sensors'], reads

    def test_suspended_graphics_devices_are_not_sampled_for_any_vendor(self):
        for vendor in ('0x1002', '0x8086', '0x10de'):
            with self.subTest(vendor=vendor):
                sensors, reads = self.gpu_sensors(vendor, 'suspended')
                self.assertEqual(sensors, {})
                self.assertEqual(reads, [])

    def test_active_internal_graphics_telemetry_is_still_sampled(self):
        sensors, reads = self.gpu_sensors('0x1002', 'active')
        self.assertEqual(sensors, {'gpu · temp1_input': 50., 'gpu · fan1_input RPM': 1200.})
        self.assertIn('temp1_input', reads)
        self.assertIn('fan1_input', reads)

    def test_active_external_graphics_are_not_sampled(self):
        sensors, reads = self.gpu_sensors('0x1002', 'active', external=True)
        self.assertEqual(sensors, {})
        self.assertEqual(reads, [])


if __name__ == '__main__':
    unittest.main()
