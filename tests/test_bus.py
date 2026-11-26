import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock

from asus_power.hardware.bus import BusClient, OBJECT, PLATFORM, SERVICE
from asus_power.hardware.fan_curves import FanCurves
from asus_power.hardware.power_limits import PowerLimits
from asus_power.hardware.profile_epp import ProfileEpp
from asus_power.hardware.rog_features import RogFeatures


class BusTests(unittest.TestCase):
    def test_feature_transports_keep_their_original_timeouts(self):
        for factory, timeout in ((ProfileEpp, 8), (RogFeatures, 8), (FanCurves, 20),
                                 (lambda runner: PowerLimits(Path('/unused'), runner), 12)):
            with self.subTest(feature=factory):
                runner = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
                factory(runner).bus.command('tree', SERVICE)
                runner.assert_called_once_with(
                    ['busctl', '--system', '--json=short', 'tree', SERVICE],
                    capture_output=True, text=True, timeout=timeout)

    def test_property_encoding_preserves_signatures_and_boolean_arrays(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
        BusClient(runner).put(OBJECT, PLATFORM, 'LedPower', '(a(ubbbb))',
                              1, 1, True, False, True, False)
        self.assertEqual(runner.call_args.args[0][-8:],
                         ['LedPower', '(a(ubbbb))', '1', '1', 'true', 'false', 'true', 'false'])

    def test_transport_decodes_json_without_coercing_feature_data(self):
        data = {'enabled': False, 'raw': [14, 254], 'unknown': None}
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps({'data': data}), ''))
        self.assertEqual(BusClient(runner).get(OBJECT, PLATFORM, 'State'), data)

    def test_empty_success_is_valid_for_commands_but_invalid_for_json_reads(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
        bus = BusClient(runner)
        self.assertEqual(bus.put(OBJECT, PLATFORM, 'EnablePptGroup', 'b', True), '')
        with self.assertRaises(json.JSONDecodeError):
            bus.get(OBJECT, PLATFORM, 'EnablePptGroup')

    def test_stderr_default_error_and_timeout_are_preserved(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 1, '', ' access denied \n'))
        bus = BusClient(runner, error='功率控制接口调用失败')
        with self.assertRaisesRegex(RuntimeError, '^access denied$'):
            bus.command('tree', SERVICE)
        runner.return_value = subprocess.CompletedProcess([], 1, '', '')
        with self.assertRaisesRegex(RuntimeError, '^功率控制接口调用失败$'):
            bus.command('tree', SERVICE)
        runner.side_effect = subprocess.TimeoutExpired('busctl', 12)
        with self.assertRaises(subprocess.TimeoutExpired):
            bus.command('tree', SERVICE)
