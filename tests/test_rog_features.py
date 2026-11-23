import unittest
from unittest.mock import Mock

from asus_power.rog_features import AURA, INTERFACE, OBJECT, RogFeatures


class RogFeaturesTests(unittest.TestCase):
    def test_aura_preserves_other_power_zones_and_mode_properties(self):
        api = RogFeatures()
        path = '/xyz/ljones/aura/device'
        before = {'path': path, 'color': [10, 20, 30], 'power': [True, True, True, True]}
        states = [True, True, False, False]
        api.read_aura = Mock(side_effect=[before, dict(before, color=[40, 50, 60], power=states)])
        api.get = Mock(side_effect=[
            [0, 0, [10, 20, 30], [1, 2, 3], 'Med', 'Right'],
            [[[1, True, True, True, True], [2, False, False, False, True]]],
        ])
        api.put = Mock()
        api.save_aura(path, [40, 50, 60], states)
        api.put.assert_any_call(path, AURA, 'LedModeData', '(uu(yyy)(yyy)ss)',
                                0, 0, 40, 50, 60, 1, 2, 3, 'Med', 'Right')
        api.put.assert_any_call(path, AURA, 'LedPower', '(a(ubbbb))',
                                2, 1, *states, 2, False, False, False, True)

    def test_saving_supply_mode_restores_active_profile_after_asusd_applies_change(self):
        api = RogFeatures()
        before = {'ac': (True, 'performance'), 'battery': (True, 'quiet')}
        desired = dict(before, battery=(True, 'balanced'))
        api.supply_rules = Mock(side_effect=[before, desired])
        api.get = Mock(side_effect=[1, 0, 1])
        api.put = Mock()
        api.save_supply(desired, ['quiet', 'balanced', 'performance'])
        api.put.assert_any_call(OBJECT, INTERFACE, 'PlatformProfileOnBattery', 'u', 0)
        api.put.assert_called_with(OBJECT, INTERFACE, 'PlatformProfile', 'u', 1)
