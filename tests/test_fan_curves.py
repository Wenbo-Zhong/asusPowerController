from copy import deepcopy
import json
import os
import subprocess
import unittest
from unittest.mock import patch
from asus_power.fan_curves import FanCurves, validate

FACTORY = {'fan': 'cpu', 'pwm': [14, 26, 51, 73, 109, 163, 163, 254],
           'temp': [40, 50, 60, 66, 71, 77, 77, 84], 'enabled': True}


class FanApiTests(unittest.TestCase):
    def setUp(self):
        self.curves = {'cpu': deepcopy(FACTORY)}
        self.writes = []
        self.fail = False
        self.ignore = False
        self.api = FanCurves(self.runner)

    def runner(self, args, **kwargs):
        method, signature, *values = args[7:]
        if method == 'FanCurveData':
            data = [[['CPU', c['pwm'], c['temp'], c['enabled']] for c in self.curves.values()]]
            output = json.dumps({'type': 'a(s(yyyyyyyy)(yyyyyyyy)b)', 'data': data})
        elif method == 'SetFanCurve':
            self.writes.append(values)
            if self.fail:
                self.fail = False
                return subprocess.CompletedProcess(args, 1, '', 'write failed')
            if self.ignore:
                self.ignore = False
            else:
                self.curves['cpu'] = {'fan': 'cpu', 'pwm': list(map(int, values[2:10])),
                                      'temp': list(map(int, values[10:18])), 'enabled': values[18] == 'true'}
            output = ''
        else:
            self.assertEqual(method, 'SetCurvesToDefaults')
            self.curves = {'cpu': dict(deepcopy(FACTORY), enabled=False)}
            output = ''
        return subprocess.CompletedProcess(args, 0, output, '')

    def test_factory_duplicates_and_raw_pwm_survive(self):
        validate(FACTORY)
        curve = self.api.read('balanced')['cpu']
        self.assertEqual(curve, FACTORY)
        self.api.save('balanced', curve)
        self.assertEqual(self.writes[0][0:2], ['0', 'CPU'])
        self.assertEqual(self.writes[0][9], '254')
        self.assertEqual(self.curves['cpu'], FACTORY)

    def test_disabled_curve_is_saved_in_the_same_call(self):
        curve = dict(deepcopy(FACTORY), enabled=False)
        self.api.save('quiet', curve)
        self.assertEqual(self.writes[0][0], '2')
        self.assertEqual(self.writes[0][-1], 'false')

    def test_readback_mismatch_restores_previous_curve(self):
        self.ignore = True
        curve = dict(deepcopy(FACTORY), enabled=False)
        with self.assertRaisesRegex(RuntimeError, '回读'):
            self.api.save('balanced', curve)
        self.assertEqual(self.curves['cpu'], FACTORY)

    def test_bad_data_never_writes(self):
        for bad in (dict(FACTORY, pwm=[0] * 7), dict(FACTORY, temp=[50, 40] + [60] * 6),
                    dict(FACTORY, pwm=[300] * 8)):
            with self.assertRaises(ValueError):
                self.api.save('balanced', bad)
        self.assertEqual(self.writes, [])

    def test_permission_error_preserves_old_curve(self):
        self.fail = True
        with self.assertRaisesRegex(RuntimeError, 'write failed'):
            self.api.save('balanced', dict(FACTORY, enabled=False))
        self.assertEqual(self.curves['cpu'], FACTORY)

    def test_restore_defaults_reloads_enabled_state(self):
        data = self.api.defaults('balanced')
        self.assertFalse(data['cpu']['enabled'])


@unittest.skipUnless(os.environ.get('ASUS_RUN_GUI_TESTS') == '1', 'opt-in desktop rendering test')
class FanEditorTests(unittest.TestCase):
    def tearDown(self):
        import gc
        gc.collect()

    def test_graph_and_fields_are_lossless_and_follow_theme(self):
        import tkinter as tk
        from types import SimpleNamespace
        from asus_power.gui import Application, NAMES
        from asus_power.fan_editor import FanEditor
        root = tk.Tk()
        with patch.object(Application, 'submit'), patch.object(Application, 'refresh_theme'), \
                patch('asus_power.gui.load', return_value={}):
            app = Application(root)
        try:
            info = {'supported': True, 'profiles': ['quiet', 'balanced', 'performance'],
                    'initial_profile': 'balanced', 'curves': {'cpu': deepcopy(FACTORY)}}
            editor = FanEditor(app, app.advanced, info, NAMES)
            app.tabs.select(1)
            root.update()
            for index in range(8):
                self.assertTrue(editor.edit_point(index))
            self.assertEqual(editor.curve, FACTORY)
            labels = [editor.canvas.itemcget(item, 'text')
                      for item in editor.canvas.find_withtag('point-number')]
            self.assertEqual(sorted(number for label in labels for number in label.split('/')),
                             list('12345678'))
            self.assertIn('6/7', labels)
            x, y = editor.point_xy(5)
            editor.select_point(SimpleNamespace(x=x, y=y))
            self.assertEqual(editor.selected, 5)
            editor.select_point(SimpleNamespace(x=x, y=y))
            self.assertEqual(editor.selected, 6)

            x, y = editor.point_xy(2)
            editor.select_point(SimpleNamespace(x=x, y=y))
            editor.drag_point(SimpleNamespace(x=x + app.px(10), y=y - app.px(10)))
            validate(editor.curve)
            self.assertTrue(editor.dirty)
            with patch('asus_power.fan_editor.messagebox.askokcancel', return_value=False):
                editor.profile.set('静音')
                editor.switch_profile()
            self.assertEqual(editor.profile.get(), '均衡')
            app.backend = SimpleNamespace(save_fan_curve=lambda *args: {'cpu': deepcopy(FACTORY)})
            editor.show_curve('balanced', 'cpu')
            with patch.object(app, 'submit') as submit:
                editor.apply()
                submit.call_args.args[0]()
            self.assertEqual(editor.curve, FACTORY)
            app.theme.apply('dark', '#ed5b00')
            root.update()
            self.assertEqual(editor.canvas.cget('bg'), app.theme.colors['card'])
        finally:
            app.close()
