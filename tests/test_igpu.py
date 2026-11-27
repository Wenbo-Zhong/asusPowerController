import subprocess
import re
from asus_power.i18n import tr
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from asus_power import igpu
from asus_power.backend import HardwareError, LinuxBackend


def od_text(minimum=600, maximum=2900):
    return f'OD_SCLK:\n0: {minimum}Mhz\n1: {maximum}Mhz\nOD_RANGE:\nSCLK: 600Mhz 2900Mhz\n'


class IgpuTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.device = self.root / 'class/drm/card7/device'
        self.device.mkdir(parents=True)
        for name, value in {'vendor': '0x1002', 'device': '0x150e',
                            'uevent': 'DRIVER=amdgpu\nPCI_SLOT_NAME=0000:66:00.0\n',
                            'pp_od_clk_voltage': od_text(),
                            'power_dpm_force_performance_level': 'auto'}.items():
            (self.device / name).write_text(value)
        self.writes = []
        self.staged = [600, 2900]
        self.fail_commit = False
        self.ignore_commit = False
        original_write = Path.write_text

        def emulate(path, text, *args, **kwargs):
            if path == self.device / 'power_dpm_force_performance_level':
                self.writes.append(text.strip())
            if path == self.device / 'pp_od_clk_voltage':
                command = text.strip()
                self.writes.append(command)
                if command.startswith('s '):
                    _, index, value = command.split()
                    self.staged[int(index)] = int(value)
                elif command == 'r':
                    self.staged[:] = [600, 2900]
                elif command == 'c':
                    if self.fail_commit:
                        self.fail_commit = False
                        raise OSError('commit failed')
                    if self.ignore_commit:
                        self.ignore_commit = False
                    else:
                        original_write(path, od_text(*self.staged))
                return len(text)
            return original_write(path, text, *args, **kwargs)

        self.emulate = emulate

    def test_discovery_uses_pci_identity_not_card_number(self):
        self.assertEqual(igpu.find_devices(self.root), {'0000:66:00.0': self.device})
        (self.device / 'device').write_text('0x73bf')
        self.assertEqual(igpu.find_devices(self.root), {})
        (self.device / 'device').write_text('0x150e')
        (self.device / 'vendor').write_text('0x10de')
        self.assertEqual(igpu.find_devices(self.root), {})

    def test_set_cap_and_restore_automatic(self):
        with patch.object(Path, 'write_text', self.emulate):
            result = igpu.change_clock(self.device, 1400)
            self.assertEqual((result['maximum'], result['mode']), (1400, 'manual'))
            result = igpu.change_clock(self.device)
        self.assertEqual((result['minimum'], result['maximum'], result['mode']), (600, 2900, 'auto'))
        self.assertEqual(self.writes, ['manual', 's 1 1400', 'c', 'manual', 'r', 'c', 'auto'])

    def test_invalid_input_and_profiling_modes_do_not_write(self):
        for maximum in (True, '1200', 599, 2901, 1000.5):
            with patch.object(Path, 'write_text', self.emulate), self.assertRaises(ValueError):
                igpu.change_clock(self.device, maximum)
        (self.device / 'power_dpm_force_performance_level').write_text('profile_peak')
        with patch.object(Path, 'write_text', self.emulate), self.assertRaises(ValueError):
            igpu.change_clock(self.device, 1000)
        self.assertEqual(self.writes, [])

    def test_voltage_formats_are_rejected_before_reset(self):
        (self.device / 'pp_od_clk_voltage').write_text(od_text() + 'OD_VDDC_CURVE:\n')
        with patch.object(Path, 'write_text', self.emulate), self.assertRaises(ValueError):
            igpu.change_clock(self.device)
        self.assertEqual(self.writes, [])

    def test_commit_failure_restores_original_cap_and_mode(self):
        self.fail_commit = True
        with patch.object(Path, 'write_text', self.emulate):
            with self.assertRaisesRegex(RuntimeError, re.escape(tr('igpu.failed_restored', error='commit failed'))):
                igpu.change_clock(self.device, 1400)
        after = igpu.clock_info(self.device)
        self.assertEqual((after['maximum'], after['mode']), (2900, 'auto'))

    def test_readback_failure_restores_original_cap_and_mode(self):
        self.ignore_commit = True
        with patch.object(Path, 'write_text', self.emulate):
            with self.assertRaisesRegex(RuntimeError, re.escape(tr('igpu.readback_mismatch'))):
                igpu.change_clock(self.device, 1400)
        self.assertEqual(igpu.clock_info(self.device)['mode'], 'auto')
        self.assertEqual(igpu.clock_info(self.device)['maximum'], 2900)

    def test_reset_failure_restores_previous_manual_limits(self):
        (self.device / 'pp_od_clk_voltage').write_text(od_text(800, 1400))
        (self.device / 'power_dpm_force_performance_level').write_text('manual')
        self.staged[:] = [800, 1400]
        self.fail_commit = True
        with patch.object(Path, 'write_text', self.emulate):
            with self.assertRaisesRegex(RuntimeError, re.escape(tr('igpu.failed_restored', error='commit failed'))):
                igpu.change_clock(self.device)
        after = igpu.clock_info(self.device)
        self.assertEqual((after['minimum'], after['maximum'], after['mode']), (800, 1400, 'manual'))

    def test_backend_authorizes_only_the_installed_helper_and_checks_result(self):
        calls = []

        def runner(arguments, **kwargs):
            calls.append(arguments)
            if '--max' in arguments:
                maximum = int(arguments[arguments.index('--max') + 1])
                with patch.object(Path, 'write_text', self.emulate):
                    igpu.change_clock(self.device, maximum)
            return subprocess.CompletedProcess(arguments, 0, 'profile battery' if '--help' in arguments else '', '')

        backend = LinuxBackend(self.root, runner, '/usr/bin/asusctl')
        with patch('asus_power.backend.trusted_helper', return_value=False):
            with self.assertRaisesRegex(HardwareError, re.escape(tr('igpu.install_first'))):
                backend.set_igpu_max(1400)
        with patch('asus_power.backend.trusted_helper', return_value=True), \
                patch('asus_power.backend.shutil.which', return_value='/usr/bin/pkexec'):
            backend.set_igpu_max(1400)
        self.assertEqual(calls[-1], ['/usr/bin/pkexec', '/usr/bin/python3', '-I', str(igpu.HELPER),
                                    '--language', tr.language, '--device', '0000:66:00.0', '--max', '1400'])
        self.assertEqual(backend.snapshot()['igpu_clock']['maximum'], 1400)
