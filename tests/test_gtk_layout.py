from contextlib import ExitStack
from copy import deepcopy
import importlib
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from gtk_fixture import gtk_controller_module
from asus_power.combinations import MODES
from asus_power.hardware.profile_epp import EPP
from asus_power.i18n import tr


class GtkLayoutTests(unittest.TestCase):
    def setUp(self):
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        module = self.context.enter_context(gtk_controller_module())
        settings = importlib.import_module(
            module.SettingsPage.__module__ if hasattr(module, 'SettingsPage')
            else module.OverviewPage.__module__)
        self.context.enter_context(patch.object(settings, 'bios_info', return_value=('test', None)))
        self.context.enter_context(patch.object(settings.backend_install, 'available', return_value=False))
        self.context.enter_context(patch.object(settings.autostart, 'entry_path', return_value=Path('/missing')))
        self.context.enter_context(patch.object(module, 'load', return_value={}))
        self.storage = self.context.enter_context(patch.object(module, 'save'))
        self.context.enter_context(patch.object(module.Controller, 'submit'))
        self.c = module.Controller(Mock(), Mock(), False)
        self.addCleanup(self.c.pool.shutdown, wait=True)
        self.addCleanup(self.c.firmware_pool.shutdown, wait=True)
        self.backend = Mock(spec=module.LinuxBackend)
        self.backend.modern = True
        self.backend.save_profile_epp.return_value = 'EPP 已保存'
        self.backend.save_supply_rules.return_value = '供电规则已保存'
        self.backend.apply_combination.return_value = '草稿已应用'
        self.caps = {
            'profiles': list(MODES), 'modern_cli': True, 'charge_limit': True, 'keyboard': True,
            'igpu_clock': {'supported': False},
            'profile_epp': {'supported': True, 'enabled': True,
                            'profiles': dict(zip(MODES, ('power', 'balance_performance', 'performance')))},
            'rog_features': {'supply': {'ac': (True, 'balanced'), 'battery': (False, 'quiet')}},
            'fan_curves': {'supported': True, 'initial_profile': 'balanced',
                           'curves': {'cpu': {'enabled': True, 'temp': list(range(10, 90, 10)),
                                             'pwm': list(range(0, 240, 30))}}},
            'power_limits': {'attributes': {
                'ppt_pl1_spl': {'label': '处理器持续功耗', 'minimum': 15, 'maximum': 80,
                               'step': 1, 'current': 35, 'default': 30}}},
        }
        self.state = {'ac': True, 'profile': 'balanced', 'battery': 60, 'limit': 80,
                      'battery_watts': 12, 'sensors': {'CPU': 45, 'CPU RPM': 2000},
                      'cpu_epp': {'energy_performance_preference': ['power', 'performance'],
                                  'scaling_governor': ['powersave']}}

    def ready(self):
        self.c.ready((self.backend, self.caps))

        def submit(operation, done=None, finished=None, executor=None, error=None):
            try:
                result = operation()
                if done:
                    done(result)
            except Exception as failure:
                self.c.status.set_label(tr('app.failed', failure=failure))
                if error is not None:
                    error.set_label(tr('app.failed', failure=failure))
            finally:
                if finished:
                    finished()

        self.c.submit = submit
        self.c.update(self.state)

    def find(self, root, text):
        matches = [widget for widget in root.walk() if widget.props.get('label') == text]
        self.assertEqual(len(matches), 1, text)
        return matches[0]

    def test_navigation_separates_daily_controls_and_settings(self):
        self.assertEqual([(name, title) for name, (title, _) in self.c.stack.pages.items()],
                         [('overview', tr('page.overview')), ('performance', tr('page.performance')),
                          ('battery', tr('page.battery')), ('settings', tr('page.settings'))])
        overview = self.c.stack.pages['overview'][1]
        self.find(overview, tr('overview.switch_title'))
        self.assertNotIn(tr('settings.startup_title'), [w.props.get('label') for w in overview.walk()])
        self.find(self.c.stack.pages['settings'][1], 'BIOS')
        save_buttons = [w for w in self.c.window.walk() if w.props.get('label') == tr('battery.save_limit')]
        self.assertEqual(len(save_buttons), 1)
        self.assertIn(save_buttons[0], list(self.c.stack.pages['battery'][1].walk()))

    def test_fan_shortcut_reveals_drafts_only_in_editor_sections(self):
        p = self.c.performance
        self.assertEqual([title for title, _ in p.sections.pages.values()],
                         [tr('performance.live'), tr('performance.modes'), tr('performance.power'), tr('performance.fans')])
        self.assertFalse(p.draft_header.get_visible())
        self.find(p.sections.pages['live'][1], tr('cooling.edit_fans')).emit('clicked')
        self.assertEqual(self.c.stack.get_visible_child_name(), 'performance')
        self.assertEqual(p.sections.get_visible_child_name(), 'fans')
        self.assertTrue(p.draft_header.get_visible())
        p.sections.set_visible_child_name('modes')
        self.assertFalse(p.draft_header.get_visible())
        self.find(p.sections.pages['modes'][1], tr('modes.save_epp'))
        p.sections.set_visible_child_name('power')
        self.assertTrue(p.draft_header.get_visible())

    def test_current_mode_highlight_follows_readings_and_shortcut_switches_hardware(self):
        self.ready()
        self.assertIn('suggested-action', self.c.overview.mode_buttons['balanced'].css)
        self.c.overview.mode_buttons['quiet'].emit('clicked')
        self.backend.set_profile.assert_called_once_with('quiet')
        self.assertIn('suggested-action', self.c.overview.mode_buttons['balanced'].css)
        self.c.update(dict(self.state, profile='quiet'))
        self.assertIn('suggested-action', self.c.overview.mode_buttons['quiet'].css)
        self.assertNotIn('suggested-action', self.c.overview.mode_buttons['balanced'].css)

    def test_epp_and_six_draft_saves_keep_independent_scopes(self):
        self.ready()
        editor, modes = self.c.performance.editor, self.c.performance.modes
        before = deepcopy(self.c.combinations)
        for choice, preference in zip(modes.epp_choices, ('power', 'balance_power', 'performance')):
            choice.set_selected(EPP.index(preference))
        modes.epp_save.emit('clicked')
        self.backend.save_profile_epp.assert_called_once_with(
            True, dict(zip(MODES, ('power', 'balance_power', 'performance'))))
        self.assertEqual(self.c.combinations, before)
        self.storage.assert_not_called()
        self.find(editor.header, tr('editor.save')).emit('clicked')
        self.assertEqual(self.storage.call_args.args[0]['combinations'], before)
        self.backend.save_profile_epp.assert_called_once()
        self.backend.apply_combination.assert_not_called()
        live = modes.epp_live.get_label()
        self.assertIn(tr('common.list_separator').join(('power', 'performance')), live)
        self.assertIn('powersave', live)

    def test_draft_switch_and_defaults_preserve_edits_without_hardware_writes(self):
        self.ready()
        editor = self.c.performance.editor
        checked, field, _, default, _ = editor.power_rows['ppt_pl1_spl']
        default.emit('clicked')
        self.assertTrue(checked.get_active())
        self.assertEqual(field.get_value_as_int(), 30)
        editor.source.set_selected(1)
        self.assertFalse(checked.get_active())
        field.set_value(20)
        checked.set_active(True)
        editor.source.set_selected(0)
        self.assertEqual(field.get_value_as_int(), 30)
        self.find(editor.header, tr('editor.save')).emit('clicked')
        entries = self.c.combinations['entries']['balanced']
        self.assertEqual(entries['ac']['power'], {'ppt_pl1_spl': 30})
        self.assertEqual(entries['battery']['power'], {'ppt_pl1_spl': 20})
        self.backend.set_profile.assert_not_called()
        self.backend.apply_combination.assert_not_called()

    def test_apply_uses_selected_draft_and_failure_releases_busy_state(self):
        self.ready()
        editor = self.c.performance.editor
        self.backend.apply_combination.side_effect = RuntimeError('组合与当前供电不一致')
        editor.source.set_selected(1)
        self.find(editor.header, tr('editor.apply')).emit('clicked')
        self.backend.apply_combination.assert_called_once_with('balanced', 'battery', {'power': {}, 'fans': {}})
        self.assertFalse(editor.busy)
        self.assertIn('组合与当前供电不一致', editor.note.get_label())
        self.storage.assert_not_called()
        self.backend.set_profile.assert_not_called()

    def test_charge_refresh_preserves_input_and_rejects_noninteger(self):
        self.ready()
        charge = self.c.battery.charge
        self.assertEqual(charge.entry.get_text(), '80')
        charge.entry.set_text('65')
        self.c.update(dict(self.state, limit=90))
        self.assertEqual(charge.entry.get_text(), '65')
        charge.action.emit('clicked')
        self.backend.set_limit.assert_called_once_with(65)
        charge.entry.set_text('bad')
        charge.action.emit('clicked')
        self.backend.set_limit.assert_called_once()
        self.assertEqual(self.c.status.get_label(), tr('battery.enter_limit'))

    def test_saving_asusd_supply_rule_disables_app_policy_after_success(self):
        self.ready()
        modes = self.c.performance.modes
        modes.auto.set_active(True)
        self.assertTrue(self.c.settings['enabled'])
        self.find(modes.supply_section, tr('modes.save_supply')).emit('clicked')
        self.backend.save_supply_rules.assert_called_once_with(
            {'ac': (True, 'balanced'), 'battery': (False, 'quiet')})
        self.assertFalse(self.c.settings['enabled'])
        self.assertFalse(modes.auto.get_active())

    def test_idle_settings_and_bios_action_use_settings_page(self):
        self.ready()
        settings = self.c.settings_page
        combinations = deepcopy(self.c.combinations)
        settings.keyboard_settings['battery'].set_selected(3)
        self.assertEqual(self.c.settings['keyboard_timeout'], {'ac': 0, 'battery': 60})
        self.assertEqual(self.c.combinations, combinations)
        self.c.submit = Mock()
        self.find(self.c.stack.pages['settings'][1], tr('settings.check_updates')).emit('clicked')
        self.assertIs(self.c.submit.call_args.kwargs['executor'], self.c.firmware_pool)
        self.assertIs(self.c.submit.call_args.kwargs['error'], settings.bios_result)
        self.c.keyboard_idle = Mock(restore_level=None)
        self.c.settings['keyboard_timeout']['ac'] = 15
        self.c.tick_keyboard()
        self.c.submit.call_args.args[1]('已检测键盘活动')
        self.assertEqual(settings.keyboard_note.get_label(), '已检测键盘活动')

    def test_missing_capabilities_leave_controls_disabled_and_build_pages(self):
        self.caps.update(modern_cli=False, charge_limit=False, keyboard=False,
                         profiles=[], fan_curves={}, profile_epp={}, rog_features={})
        self.ready()
        self.assertFalse(self.c.battery.charge.action.get_sensitive())
        self.assertFalse(self.c.battery.full_charge.get_sensitive())
        self.assertTrue(all(not w.get_sensitive() for w in self.c.settings_page.light_buttons))
        self.assertFalse(self.c.performance.modes.epp_card.get_sensitive())
        self.assertFalse(self.c.performance.editor.reset_fans.get_sensitive())
