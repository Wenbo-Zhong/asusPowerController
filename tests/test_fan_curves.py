from copy import deepcopy
import json
import subprocess
import unittest
from asus_power.hardware.fan_curves import FanCurves, validate

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
