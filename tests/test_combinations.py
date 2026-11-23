from copy import deepcopy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from asus_power.combinations import (CombinationApplier, CombinationPolicy, defaults,
                                     effective, normalized, validate, MODES, SOURCES)

CURVE = {'fan': 'cpu', 'temp': [30, 40, 50, 60, 70, 75, 80, 84],
         'pwm': [0, 20, 40, 80, 110, 150, 200, 254], 'enabled': True}


class ConfigurationTests(unittest.TestCase):
    def test_legacy_epp_is_ignored_without_losing_power_or_fans(self):
        legacy = defaults()
        legacy['enabled'] = True
        legacy['epp_values'] = {'power': 230}
        entry = legacy['entries']['balanced']['ac']
        entry.update(power={'ppt_pl1_spl': 40}, fans={'cpu': CURVE}, epp=173)
        config = normalized(legacy)
        self.assertNotIn('epp_values', config)
        self.assertEqual(effective(config, 'balanced', 'ac'),
                         {'power': {'ppt_pl1_spl': 40}, 'fans': {'cpu': CURVE}})
        policy = CombinationPolicy()
        state = {'profile': 'balanced', 'ac': True}
        self.assertEqual(policy.request(state, config)[:2], ('balanced', 'ac'))
        self.assertIsNone(policy.request(state, config))
        self.assertEqual(policy.request(dict(state, ac=False), config)[2], {'power': {}, 'fans': {}})


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.context = {'profile': 'balanced', 'ac': True}
        backend = SimpleNamespace(root='/unused-fixture', runner=Mock(),
                                  combination_context=lambda: dict(self.context))
        self.api = CombinationApplier(backend)
        self.values = {'ppt_pl1_spl': {'current': 60, 'minimum': 15, 'maximum': 80, 'step': 5},
                       'ppt_pl2_sppt': {'current': 70, 'minimum': 15, 'maximum': 80, 'step': 5}}
        self.curves = {'cpu': deepcopy(CURVE)}
        self.enabled = False
        self.calls = []
        def set_enabled(value):
            self.enabled = value
            self.calls.append(('enabled', value))
        def write(name, value):
            self.values[name]['current'] = value
            self.calls.append(('power', name, value))
        def apply_power(name, value):
            write(name, value)
            return '功率已应用'
        def save_fan(mode, curve):
            self.curves[curve['fan']] = deepcopy(curve)
            self.calls.append(('fan', curve['enabled']))
        def restore_fans(mode, curves):
            self.curves.update(deepcopy(curves))
            self.calls.append(('restore_fans',))
            return []
        self.api.power = SimpleNamespace(gpu_block_reason=lambda: '', read_attributes=lambda: deepcopy(self.values),
            enabled=lambda: self.enabled, pending=lambda name: None,
            set_enabled=set_enabled, apply=apply_power, write=write)
        self.api.fans = SimpleNamespace(read=lambda mode: deepcopy(self.curves), save=save_fan, restore=restore_fans)
        self.entry = {'power': {'ppt_pl1_spl': 40}, 'fans': {'cpu': dict(deepcopy(CURVE), enabled=False)}}

    def test_applies_all_components_and_checks_readbacks(self):
        message = self.api.apply('balanced', 'ac', self.entry)
        self.assertIn('已应用 均衡 · 插电', message)
        self.assertEqual(self.values['ppt_pl1_spl']['current'], 40)
        self.assertFalse(self.curves['cpu']['enabled'])

    def test_preflight_rejects_mismatch_bounds_or_step_without_writes(self):
        with self.assertRaises(RuntimeError):
            self.api.apply('balanced', 'battery', self.entry)
        for value in (10, 43, 85):
            entry = dict(self.entry, power={'ppt_pl1_spl': value})
            with self.assertRaises(ValueError):
                self.api.apply('balanced', 'ac', entry)
        self.assertEqual(self.calls, [])

    def test_last_component_failure_restores_power_fans_and_enable_flag(self):
        self.api.fans.save = Mock(side_effect=RuntimeError('fan denied'))
        with self.assertRaisesRegex(RuntimeError, '已恢复原设置'):
            self.api.apply('balanced', 'ac', self.entry)
        self.assertEqual(self.values['ppt_pl1_spl']['current'], 60)
        self.assertEqual(self.curves['cpu'], CURVE)
        self.assertFalse(self.enabled)

    def test_power_transition_aborts_without_restoring_old_values_to_new_context(self):
        original = self.api.power.apply
        def transition(name, value):
            result = original(name, value)
            self.context['ac'] = False
            return result
        self.api.power.apply = transition
        with self.assertRaisesRegex(RuntimeError, '未向新组合写回旧值'):
            self.api.apply('balanced', 'ac', self.entry)
        self.assertEqual(self.values['ppt_pl1_spl']['current'], 40)
        self.assertNotIn(('power', 'ppt_pl1_spl', 60), self.calls)

    def test_queued_gpu_limit_is_reported_without_waking_gpu(self):
        self.values['nv_tgp'] = {'current': 75, 'minimum': 55, 'maximum': 85, 'step': 5}
        self.enabled = True
        pending = {}
        self.api.power.pending = lambda name: pending.get(name)
        def queued(name, value):
            pending[name] = value
            return '已排队，独显唤醒后生效'
        self.api.power.apply = queued
        result = self.api.apply('balanced', 'ac', {'power': {'nv_tgp': 80}, 'fans': {}})
        self.assertIn('排队', result)
        self.assertEqual(self.values['nv_tgp']['current'], 75)

    def test_gpu_manager_leaves_saved_gpu_values_and_applies_cpu_and_fans(self):
        self.api.power.gpu_block_reason = lambda: '独显由 USB4 GPU Manager 管理'
        self.entry['power']['nv_tgp'] = 80
        message = self.api.apply('balanced', 'ac', self.entry)
        self.assertIn('USB4 GPU Manager', message)
        self.assertIn(('power', 'ppt_pl1_spl', 40), self.calls)
        self.assertIn(('fan', False), self.calls)
        self.assertNotIn(('power', 'nv_tgp', 80), self.calls)
        self.assertEqual(self.entry['power']['nv_tgp'], 80)

    def test_empty_entry_leaves_all_hardware_unchanged(self):
        self.api.apply('balanced', 'ac', {'power': {}, 'fans': {}})
        self.assertEqual(self.calls, [])

    def test_enabling_group_preserves_unselected_power_values(self):
        original = self.api.power.set_enabled
        def enable(value):
            original(value)
            if value:
                self.values['ppt_pl2_sppt']['current'] = 80
        self.api.power.set_enabled = enable
        self.api.apply('balanced', 'ac', self.entry)
        self.assertEqual(self.values['ppt_pl2_sppt']['current'], 70)

    def test_late_override_is_failure_and_restores_earlier_components(self):
        save = self.api.fans.save
        def override(mode, curve):
            save(mode, curve)
            self.values['ppt_pl1_spl']['current'] = 80
        self.api.fans.save = override
        with self.assertRaisesRegex(RuntimeError, '外部服务修改'):
            self.api.apply('balanced', 'ac', self.entry)
        self.assertEqual(self.values['ppt_pl1_spl']['current'], 60)
        self.assertEqual(self.curves['cpu'], CURVE)


@unittest.skipUnless(os.environ.get('ASUS_RUN_GUI_TESTS') == '1', 'opt-in desktop test')
class CombinationGuiTests(unittest.TestCase):
    def tearDown(self):
        import gc
        gc.collect()

    def test_six_drafts_and_fans_round_trip_without_hardware_writes(self):
        import tkinter as tk
        from asus_power.gui import Application
        root = tk.Tk()
        with patch.object(Application, 'submit'), patch.object(Application, 'refresh_theme'), \
                patch('asus_power.gui.load', return_value={}):
            app = Application(root)
            try:
                backend = Mock()
                caps = {'model': '测试', 'profiles': list(MODES), 'modern_cli': True,
                        'charge_limit': False, 'keyboard': False, 'fans': ['cpu'],
                        'fan_curves': {'supported': True, 'profiles': list(MODES),
                                      'initial_profile': 'balanced', 'curves': {'cpu': deepcopy(CURVE),
                                                                             'gpu': dict(deepcopy(CURVE), fan='gpu')}},
                        'power_limits': {'supported': True, 'enabled': True,
                            'context': {'profile': 'balanced'}, 'attributes': {
                                'ppt_pl1_spl': {'label': '处理器持续功率', 'current': 60, 'minimum': 15,
                                               'maximum': 80, 'step': 5, 'default': 60}}}}
                app.ready(backend, caps)
                app.tabs.select(app.combination_page)
                root.update()
                panel = app.combination_panel
                panel.power_rows['ppt_pl1_spl'][0].set(True)
                panel.power_rows['ppt_pl1_spl'][1].set(40)
                panel.fan_enabled.set(True)
                panel.editor.fields[2][1].set('20.0')
                panel.editor.apply()  # Draft apply must never call the backend.
                backend.save_fan_curve.assert_not_called()
                panel.draft['entries']['balanced']['battery']['power']['ppt_pl1_spl'] = 35
                panel.copy_fans()
                copied = panel.draft['entries']['balanced']['battery']
                self.assertEqual(copied['fans']['cpu']['pwm'][2], 51)
                self.assertEqual(copied['power']['ppt_pl1_spl'], 35)
                panel.editor.fields[2][1].set('22.0')
                with patch('asus_power.combination_panel.messagebox.askokcancel', return_value=False) as ask:
                    panel.copy_fans()
                    ask.assert_called_once()
                self.assertEqual(copied['fans']['cpu']['pwm'][2], 51)
                panel.editor.fields[2][1].set('20.0')
                panel.editor.apply()

                panel.source.set('离电')
                panel.switch()
                panel.source.set('插电')
                panel.switch()
                self.assertEqual(panel.power_rows['ppt_pl1_spl'][1].get(), 40)
                panel.collect()
                saved = panel.draft
                self.assertEqual(saved['entries']['balanced']['ac']['fans']['cpu']['pwm'][-1], 254)
                # Page-level save must commit the selected fan before switching
                # to edit a second fan; otherwise the first curve gets lost.
                self.assertFalse(panel.editor.dirty)
                panel.editor.fan.set('GPU 风扇')
                with patch('asus_power.fan_editor.messagebox.askokcancel') as ask:
                    panel.editor.switch_fan()
                    ask.assert_not_called()
                panel.editor.fields[2][1].set('21.0')
                panel.collect()
                self.assertEqual(panel.draft['entries']['balanced']['ac']['fans']['cpu']['pwm'][2], 51)
                self.assertEqual(panel.draft['entries']['balanced']['ac']['fans']['gpu']['pwm'][2], 54)

                with patch('asus_power.gui.save') as persist:
                    panel.save()
                    self.assertIn('combinations', persist.call_args.args[0])
                    app.save_policy()
                    self.assertIn('combinations', persist.call_args.args[0])
                app.theme.apply('dark')
                root.geometry(f'{app.px(720)}x{app.px(520)}')
                root.update()
                app.open_combination_section(panel.fan_card)
                root.update()
                viewport = panel.fan_card.master.master
                viewport.yview_moveto(1)
                root.update()
                self.assertEqual(viewport.yview()[1], 1)
                self.assertTrue(panel.mode_combo.winfo_viewable())
                app.open_combination_section(panel.epp_card)
                root.update()
                self.assertTrue(panel.epp_card.winfo_viewable())
                backend.apply_combination.assert_not_called()
            finally:
                app.close()
