import json
import subprocess
import unittest
from asus_power.hardware.profile_epp import EPP, LINK, PROPERTIES, ProfileEpp


class ProfileEppTests(unittest.TestCase):
    def setUp(self):
        self.properties = {LINK: {'type': 'b', 'data': True},
                           'ProfileQuietEpp': {'type': 'u', 'data': 4},
                           'ProfileBalancedEpp': {'type': 'u', 'data': 2},
                           'ProfilePerformanceEpp': {'type': 'u', 'data': 1}}
        self.writes = []
        self.fail_on = None

        def runner(args, **kwargs):
            if 'set-property' in args:
                name, signature, text = args[-3:]
                self.writes.append(name)
                if name == self.fail_on:
                    return subprocess.CompletedProcess(args, 1, '', 'D-Bus denied')
                self.properties[name] = {'type': signature, 'data': text == 'true' if signature == 'b' else int(text)}
                output = ''
            else:
                output = json.dumps({'type': 'a{sv}', 'data': [self.properties]})
            return subprocess.CompletedProcess(args, 0, output, '')
        self.api = ProfileEpp(runner)

    def test_reads_rcc_enum_mapping(self):
        self.assertEqual(self.api.read()['profiles'],
                         {'quiet': 'power', 'balanced': 'balance_performance', 'performance': 'performance'})

    def test_validates_all_settings_before_writing(self):
        profiles = self.api.read()['profiles']
        profiles['balanced'] = '128'
        with self.assertRaises(ValueError):
            self.api.save(True, profiles)
        self.assertEqual(self.writes, [])

    def test_saves_persistent_asusd_rules_and_link_switch(self):
        profiles = {'quiet': 'balance_power', 'balanced': 'balance_power', 'performance': 'performance'}
        self.api.save(False, profiles)
        self.assertEqual(self.api.read()['profiles'], profiles)
        self.assertFalse(self.api.read()['enabled'])
        self.assertEqual(self.writes[-1], LINK)

    def test_mid_save_error_restores_earlier_properties(self):
        old = self.api.read()
        profiles = dict(old['profiles'], quiet='balance_power', balanced='balance_power')
        self.fail_on = 'ProfileBalancedEpp'
        with self.assertRaisesRegex(RuntimeError, 'D-Bus denied'):
            self.api.save(True, profiles)
        self.assertEqual(self.api.read(), old)

    def test_unknown_enum_is_unavailable(self):
        self.properties['ProfileBalancedEpp']['data'] = 99
        self.assertFalse(self.api.status()['supported'])
