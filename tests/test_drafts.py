from copy import deepcopy
import unittest

from asus_power.combinations import MODES, SOURCES, defaults
from asus_power.ui.drafts import CombinationDraft, CurveDraft, pwm_percent

FACTORY = {'fan': 'cpu', 'temp': [40, 50, 60, 66, 71, 77, 77, 84],
           'pwm': [14, 26, 51, 73, 109, 163, 163, 254], 'enabled': True}


class CurveDraftTests(unittest.TestCase):
    def test_display_and_save_preserve_every_raw_pwm_value(self):
        for value in range(256):
            with self.subTest(pwm=value):
                curve = dict(FACTORY, pwm=[value] * 8)
                draft = CurveDraft()
                draft.load({'cpu': curve})
                draft.edit(curve['temp'], [pwm_percent(value)] * 8, True)
                self.assertEqual(draft.export(True), {'cpu': curve})

    def test_edit_changes_only_the_explicitly_edited_output(self):
        draft = CurveDraft()
        draft.load({'cpu': FACTORY})
        percent = [pwm_percent(value) for value in FACTORY['pwm']]
        percent[0] = 10.
        draft.edit(FACTORY['temp'], percent, False)
        expected = dict(FACTORY, pwm=[26, *FACTORY['pwm'][1:]], enabled=False)
        self.assertEqual(draft.export(True), {'cpu': expected})
        self.assertEqual(FACTORY['pwm'][0], 14)

    def test_invalid_edit_keeps_previous_valid_curve_and_selection(self):
        draft = CurveDraft()
        draft.load({'cpu': FACTORY})
        with self.assertRaises(ValueError):
            draft.edit([80, *FACTORY['temp'][1:]], [pwm_percent(v) for v in FACTORY['pwm']], True)
        self.assertEqual(draft.active, 'cpu')
        self.assertEqual(draft.export(True), {'cpu': FACTORY})

    def test_switch_and_export_keep_fans_independent(self):
        draft = CurveDraft()
        gpu = dict(FACTORY, fan='gpu', enabled=False)
        draft.load({'cpu': FACTORY, 'gpu': gpu})
        draft.select('gpu')
        draft.edit(gpu['temp'], [pwm_percent(v) for v in gpu['pwm']], True)
        output = draft.export(True)
        self.assertTrue(output['gpu']['enabled'])
        self.assertEqual(output['cpu'], FACTORY)
        output['gpu']['pwm'][0] = 0
        self.assertEqual(draft.export(True)['gpu']['pwm'][0], 14)

    def test_unmanaged_empty_draft_is_allowed_but_managed_requires_data(self):
        draft = CurveDraft()
        self.assertEqual(draft.export(False), {})
        with self.assertRaisesRegex(ValueError, '读取方案曲线'):
            draft.export(True)

    def test_reload_preserves_existing_fan_selection_and_clears_missing_fans(self):
        draft = CurveDraft()
        draft.load({'cpu': FACTORY, 'gpu': dict(FACTORY, fan='gpu')})
        draft.select('gpu')
        draft.load({'gpu': dict(FACTORY, fan='gpu')})
        self.assertEqual(draft.active, 'gpu')
        draft.load({})
        self.assertIsNone(draft.active)


class CombinationDraftTests(unittest.TestCase):
    def test_all_six_drafts_switch_without_modifying_persisted_settings(self):
        saved = defaults()
        draft = CombinationDraft(saved)
        expected = deepcopy(saved)
        for index, (mode, source) in enumerate((m, s) for m in MODES for s in SOURCES):
            draft.select(mode, source)
            draft.collect({'ppt_pl1_spl': 20 + index}, {}, True)
            expected['entries'][mode][source]['power'] = {'ppt_pl1_spl': 20 + index}
        expected['enabled'] = True
        self.assertEqual(draft.export(), expected)
        self.assertEqual(saved, defaults())

    def test_collect_preserves_unavailable_attributes_and_removes_only_unchecked_ones(self):
        settings = defaults()
        settings['entries']['balanced']['ac']['power'] = {'ppt_pl1_spl': 40, 'nv_tgp': 80}
        draft = CombinationDraft(settings)
        draft.collect({'ppt_pl1_spl': None, 'ppt_pl2_sppt': 50}, {}, False)
        self.assertEqual(draft.entry['power'], {'nv_tgp': 80, 'ppt_pl2_sppt': 50})

    def test_copy_preserves_other_supply_power_and_isolates_curve_arrays(self):
        settings = defaults()
        settings['entries']['balanced']['battery']['power'] = {'ppt_pl1_spl': 25}
        draft = CombinationDraft(settings)
        curves = {'cpu': deepcopy(FACTORY)}
        target = draft.other_supply()
        draft.replace_fans(target, curves)
        curves['cpu']['pwm'][0] = 0
        draft.select(*target)
        self.assertEqual(draft.entry, {'power': {'ppt_pl1_spl': 25}, 'fans': {'cpu': FACTORY}})
        self.assertEqual(settings['entries']['balanced']['battery']['fans'], {})

    def test_async_curve_result_updates_the_captured_draft_after_selection_changes(self):
        draft = CombinationDraft(defaults())
        captured = draft.selected
        draft.select('quiet', 'battery')
        draft.replace_fans(captured, {'cpu': FACTORY})
        self.assertEqual(draft.entry['fans'], {})
        draft.select(*captured)
        self.assertEqual(draft.entry['fans'], {'cpu': FACTORY})

    def test_read_templates_remain_unmanaged_and_exports_do_not_alias_draft(self):
        draft = CombinationDraft(defaults())
        draft.templates['balanced'] = {'cpu': deepcopy(FACTORY)}
        self.assertEqual(draft.curves(), {'cpu': FACTORY})
        self.assertEqual(draft.export()['entries']['balanced']['ac']['fans'], {})
        draft.collect({}, draft.curves(), True)
        output = draft.export()
        output['entries']['balanced']['ac']['fans']['cpu']['pwm'][0] = 0
        self.assertEqual(draft.entry['fans']['cpu']['pwm'][0], 14)
