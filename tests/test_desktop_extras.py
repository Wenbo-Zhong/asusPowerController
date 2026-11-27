import json
from asus_power.i18n import tr
import subprocess
import unittest
from unittest.mock import Mock

from asus_power.hardware.firmware import check_bios_updates
from asus_power.keyboard_idle import KeyboardIdle


class DesktopExtrasTests(unittest.TestCase):
    def test_backlight_timeout_restores_brightness_and_respects_manual_changes(self):
        keyboard = KeyboardIdle(None, None, None)
        keyboard.call = Mock(return_value=(30_000,))
        keyboard.brightness = Mock(side_effect=(33, 0, 67, 100))
        keyboard.set_brightness = Mock()

        keyboard.update(15)
        keyboard.update(0)
        keyboard.update(15)
        keyboard.update(0)

        self.assertEqual([call.args[0] for call in keyboard.set_brightness.call_args_list], [0, 33, 0])
        self.assertIsNone(keyboard.restore_level)

    def test_fwupd_checks_system_bios_only(self):
        bios = {'Name': 'System Firmware', 'Plugin': 'uefi_capsule', 'DeviceId': 'bios'}
        runner = Mock(side_effect=[
            subprocess.CompletedProcess([], 0, json.dumps({'Devices': [bios]}), ''),
            subprocess.CompletedProcess([], 2, '', ''),
            subprocess.CompletedProcess([], 0, json.dumps({'Devices': [
                {'DeviceId': 'dbx', 'Releases': [{'Version': '2026'}]},
                {'DeviceId': 'bios', 'Releases': [{'Version': '321'}]},
            ]}), ''),
        ])
        self.assertEqual(check_bios_updates(runner), tr('firmware.available_prefix') + '321')
        self.assertEqual(runner.call_args.args[0], ['fwupdmgr', 'get-updates', 'bios', '--json'])
