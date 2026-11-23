import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from asus_power.battery import read_battery
from asus_power.battery_panel import formatted_values


class BatteryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'BAT1'
        self.path.mkdir()

    def put(self, **values):
        for key, value in values.items():
            (self.path / key).write_text(str(value))

    def test_native_energy_health_and_discharge_estimate(self):
        self.put(status='Discharging', capacity=60, energy_now=45000000,
                 energy_full=75000000, energy_full_design=90000000,
                 power_now=15000000, cycle_count=42)
        data = read_battery(self.path)
        self.assertAlmostEqual(data['health_percent'], 75 / 90 * 100)
        self.assertEqual(data['time_seconds'], 3 * 3600)
        self.assertEqual(data['time_kind'], 'discharging')
        self.assertEqual(data['power_w'], 15)
        self.assertEqual(data['cycle_count'], 42)
        self.assertFalse(data['energy_converted'])

    def test_charge_only_battery_with_current_and_voltage(self):
        self.put(status='Discharging', charge_now=3000000, charge_full=5000000,
                 charge_full_design=6000000, current_now=-1000000,
                 voltage_now=16000000, voltage_min_design=15000000, cycle_count=0)
        data = read_battery(self.path)
        self.assertEqual(data['power_w'], 16)
        self.assertEqual(data['capacity_percent'], 60)
        self.assertEqual(data['energy_full_wh'], 75)
        self.assertEqual(data['energy_design_wh'], 90)
        self.assertEqual(data['time_seconds'], 10800)
        self.assertEqual(data['cycle_count'], 0)
        self.assertTrue(data['energy_converted'])
        self.assertIn('0 次', formatted_values(data)['cycles'])

    def test_charge_time_uses_limit_not_100_percent(self):
        self.put(status='Charging', energy_now=45000000, energy_full=80000000,
                 power_now=10000000, charge_control_end_threshold=80)
        data = read_battery(self.path)
        self.assertEqual(data['time_seconds'], (64 - 45) / 10 * 3600)
        self.assertEqual(data['time_target_percent'], 80)
        shown = formatted_values(data)
        self.assertEqual(shown['time_title'], '预计充至 80%')
        self.assertEqual(shown['power_title'], '充电功率')
        self.assertEqual(shown['time'], '约 1 小时 54 分钟')
        self.put(energy_now=65000000)
        self.assertIsNone(read_battery(self.path)['time_seconds'])

    def test_no_mixing_of_energy_and_charge_for_health(self):
        self.put(energy_full=75000000, charge_full=5000000, charge_full_design=6000000)
        self.assertAlmostEqual(read_battery(self.path)['health_percent'], 5 / 6 * 100)
        (self.path / 'charge_full').unlink()
        self.assertIsNone(read_battery(self.path)['health_percent'])

    def test_bad_numbers_and_zero_denominators_are_not_telemetry(self):
        self.put(capacity='nan', energy_now='inf', energy_full=0, energy_full_design=0,
                 current_now='nan', voltage_now='bad', power_now='inf', cycle_count=-1)
        data = read_battery(self.path)
        for key in ('capacity_percent', 'health_percent', 'time_seconds', 'power_w', 'cycle_count'):
            self.assertIsNone(data[key], key)
        for value in data.values():
            if isinstance(value, float):
                self.assertTrue(math.isfinite(value))

    def test_overflow_in_derived_values_stays_unknown(self):
        self.put(current_now='1e308', voltage_now='1e308', charge_full='1e308')
        data = read_battery(self.path)
        self.assertIsNone(data['power_w'])
        self.assertIsNone(data['energy_full_wh'])

    def test_missing_battery_and_missing_sensors(self):
        self.assertFalse(read_battery(None)['present'])
        self.assertFalse(read_battery(self.path / 'missing')['present'])
        data = read_battery(self.path)
        self.assertTrue(data['present'])
        self.assertEqual(formatted_values(data)['health'], '—')
        self.put(present=0)
        self.assertFalse(read_battery(self.path)['present'])
        self.assertEqual(formatted_values(read_battery(self.path))['summary'], '未检测到电池')

    def test_full_paused_and_unknown_status_have_no_eta(self):
        self.put(energy_now=45000000, energy_full=80000000, power_now=10000000)
        for status in ('Full', 'Not charging', 'Unknown', 'Unexpected'):
            with self.subTest(status=status):
                self.put(status=status)
                data = read_battery(self.path)
                self.assertIsNone(data['time_seconds'])
                self.assertEqual(formatted_values(data)['time'], '—')

    def test_low_or_zero_rate_has_no_infinite_estimate(self):
        self.put(status='Discharging', energy_now=45000000)
        for power in (0, 50000):
            self.put(power_now=power)
            self.assertIsNone(read_battery(self.path)['time_seconds'])

    def test_invalid_direct_power_falls_back_to_current(self):
        self.put(power_now='bad', current_now=1000000, voltage_now=16000000)
        self.assertEqual(read_battery(self.path)['power_w'], 16)
        self.put(power_now=0)
        self.assertEqual(read_battery(self.path)['power_w'], 0)

    def test_negative_power_is_a_magnitude_and_status_selects_direction(self):
        self.put(status='Charging', power_now=-10000000, energy_now=40000000, energy_full=80000000)
        data = read_battery(self.path)
        self.assertEqual(data['power_w'], 10)
        self.assertEqual(formatted_values(data)['power_title'], '充电功率')
        self.assertEqual(data['time_seconds'], 14400)


@unittest.skipUnless(os.environ.get('ASUS_RUN_GUI_TESTS') == '1', 'opt-in desktop test')
class BatteryGuiTests(unittest.TestCase):
    def tearDown(self):
        import gc
        gc.collect()

    def test_battery_tab_readings_theme_scroll_and_shared_limit(self):
        import tkinter as tk
        from asus_power.gui import Application
        root = tk.Tk()
        with patch.object(Application, 'submit'), patch.object(Application, 'refresh_theme'), \
                patch('asus_power.gui.load', return_value={}):
            app = Application(root)
            try:
                caps = {'model': '测试', 'profiles': ['quiet', 'balanced', 'performance'],
                        'modern_cli': True, 'charge_limit': True, 'keyboard': False, 'fans': [],
                        'battery_info': {'present': True, 'limit_percent': 90}}
                app.ready(SimpleNamespace(capabilities=lambda: caps, snapshot=lambda: {}))
                app.tabs.select(app.battery_page.master.master)
                root.update()
                self.assertEqual(app.limit.get(), 90)
                panel = app.battery_panel
                info = {'present': True, 'name': 'BAT1', 'status': 'Discharging',
                        'capacity_percent': 60, 'health_percent': 87, 'power_w': 15,
                        'time_seconds': 7200, 'cycle_count': 0, 'limit_percent': 80}
                panel.refresh(info)
                root.update()
                self.assertEqual(panel.variables['health'].get(), '87.0%')
                self.assertEqual(panel.variables['power_title'].get(), '放电功率')
                self.assertEqual(panel.variables['time'].get(), '约 2 小时 0 分钟')
                # Identical polling results must not retrigger Tk variable traces/layout.
                changed = []
                panel.variables['power'].trace_add('write', lambda *args: changed.append(1))
                panel.refresh(dict(info))
                self.assertEqual(changed, [])
                app.theme.apply('dark')
                root.geometry(f'{app.px(720)}x{app.px(520)}')
                root.update()
                viewport = app.battery_page.master
                viewport.yview_moveto(1)
                root.update()
                self.assertEqual(viewport.yview()[1], 1)
                self.assertGreater(viewport.content_height, viewport.winfo_height())
                panel.refresh({'present': False})
                self.assertEqual(panel.variables['summary'].get(), '未检测到电池')
                self.assertEqual(panel.variables['health'].get(), '—')
            finally:
                app.close()


class BatteryBackendTests(unittest.TestCase):
    def test_snapshot_converts_power_and_rediscovers_system_battery(self):
        import subprocess
        from asus_power.backend import LinuxBackend
        with TemporaryDirectory() as directory:
            root = Path(directory)
            def put(name, value):
                path = root / 'class/power_supply' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(value))
            put('BAT1/type', 'Battery')
            put('BAT1/present', 0)
            put('mouse/type', 'Battery')
            put('mouse/scope', 'Device')
            put('mouse/capacity', 99)
            runner = lambda args, **kw: subprocess.CompletedProcess(
                args, 0 if '--help' in args else 1, 'profile battery' if '--help' in args else '', 'unavailable')
            backend = LinuxBackend(root, runner, '/nonexistent-asusctl')
            with patch.object(backend, 'power_limits', return_value={}), \
                    patch.object(backend, 'igpu_clock', return_value={}):
                self.assertFalse(backend.snapshot()['battery_info']['present'])
                for name, value in {'type': 'Battery', 'present': 1, 'capacity': 60,
                                    'current_now': 1000000, 'voltage_now': 16000000,
                                    'charge_control_end_threshold': 80}.items():
                    put('BAT1/' + name, value)
                state = backend.snapshot()
                self.assertEqual(state['battery'], 60)
                self.assertEqual(state['battery_watts'], 16)
                self.assertEqual(state['limit'], 80)
                self.assertEqual(state['battery_info']['name'], 'BAT1')
