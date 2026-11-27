"""供电切换策略与独立 EPP 编辑；EPP 直接保存到 asusd，不纳入六套组合。"""
from ..i18n import tr
from ..combinations import MODES
from ..hardware.profile_epp import EPP
from .gtk import Gtk
from .widgets import EPP_NAMES, MODENAMES, box, button, card, dropdown, flow, label


class ModeSettings:
    def __init__(self, controller, page):
        self.controller = controller
        auto = card(page, tr('modes.power_title'))
        auto.append(label(tr('modes.app_rule_note'), True))
        self.auto = Gtk.CheckButton(label=tr('modes.auto'), active=bool(controller.settings.get('enabled')))
        auto.append(self.auto)
        self.auto.connect('toggled', lambda *_: self.save_policy())
        self.policy_choices = []
        for text in (tr('supply.ac'), tr('supply.battery')):
            row = box(True)
            caption = label(text, True)
            caption.set_width_chars(4)
            row.append(caption)
            choice = dropdown([])
            choice.set_hexpand(True)
            choice.set_sensitive(False)
            row.append(choice)
            auto.append(row)
            self.policy_choices.append(choice)
        self.supply_section = Gtk.Expander(label=tr('modes.asusd_title'), visible=False)
        auto.append(self.supply_section)
        self.epp_card = card(page, tr('modes.epp_title'))
        self.epp_card.append(label(tr('modes.epp_note'), True))
        self.epp_live = label(tr('modes.epp_unknown'), True)
        self.epp_card.append(self.epp_live)
        self.epp_linked = Gtk.CheckButton(label=tr('modes.epp_link'))
        self.epp_card.append(self.epp_linked)
        columns = flow(homogeneous=True)
        self.epp_card.append(columns)
        self.epp_choices = []
        for mode in MODES:
            group = box()
            columns.insert(group, -1)
            group.append(label(MODENAMES[mode]))
            choice = dropdown(EPP_NAMES)
            group.append(choice)
            self.epp_choices.append(choice)
        self.epp_save = button(tr('modes.save_epp'), self.save_epp)
        self.epp_save.set_halign(Gtk.Align.START)
        self.epp_card.append(self.epp_save)
        self.epp_card.set_sensitive(False)

    def ready(self, caps):
        modes = self.controller.mode_keys
        for choice, key, default in zip(self.policy_choices, ('ac', 'battery'), ('balanced', 'quiet')):
            choice.set_model(Gtk.StringList.new([MODENAMES.get(mode, mode) for mode in modes]))
            desired = self.controller.settings.get(key, default)
            choice.set_selected(modes.index(desired) if desired in modes else Gtk.INVALID_LIST_POSITION)
            choice.set_sensitive(bool(modes))
            choice.connect('notify::selected', lambda *_: self.save_policy())
        self.populate_supply(caps.get('rog_features', {}).get('supply'))
        info = caps.get('profile_epp', {})
        if info.get('supported'):
            self.epp_card.set_sensitive(True)
            self.epp_linked.set_active(info.get('enabled', False))
            for mode, choice in zip(MODES, self.epp_choices):
                choice.set_selected(EPP.index(info['profiles'].get(mode, 'default')))
            if info.get('ppd_active'):
                self.epp_card.append(label(tr('modes.external_epp_note'), True))
        else:
            self.epp_card.append(label(info.get('reason', tr('modes.epp_unavailable')), True))

    def save_policy(self):
        values = {'enabled': self.auto.get_active()}
        for key, choice in zip(('ac', 'battery'), self.policy_choices):
            index = choice.get_selected()
            if index < len(self.controller.mode_keys):
                values[key] = self.controller.mode_keys[index]
        self.controller.persist(**values)

    def save_epp(self):
        profiles = {mode: EPP[choice.get_selected()] for mode, choice in zip(MODES, self.epp_choices)}
        linked = self.epp_linked.get_active()
        self.controller.perform(lambda: self.controller.backend.save_profile_epp(linked, profiles),
                                control=self.epp_save)

    def populate_supply(self, supply):
        c = self.controller
        if not supply or not c.mode_keys:
            return
        content = box()
        self.supply_section.set_child(content)
        self.supply_section.set_visible(True)
        content.append(label(tr('modes.asusd_note'), True))
        rule_widgets = {}
        for source, title in [('ac', tr('supply.ac')), ('battery', tr('supply.battery'))]:
            enabled, current = supply[source]
            row = box(True)
            toggle = Gtk.CheckButton(label=title, active=enabled)
            selector = dropdown([MODENAMES.get(mode, mode) for mode in c.mode_keys])
            selector.set_selected(c.mode_keys.index(current) if current in c.mode_keys
                                  else Gtk.INVALID_LIST_POSITION)
            selector.set_hexpand(True)
            row.append(toggle)
            row.append(selector)
            content.append(row)
            rule_widgets[source] = toggle, selector

        def save_supply():
            rules = {}
            for source, (toggle, selector) in rule_widgets.items():
                index = selector.get_selected()
                if index >= len(c.mode_keys):
                    c.status.set_label(tr('modes.select_sources'))
                    return
                rules[source] = toggle.get_active(), c.mode_keys[index]

            def done(message):
                if any(enabled for enabled, _ in rules.values()):
                    self.auto.set_active(False)
                c.status.set_label(message)

            c.perform(lambda: c.backend.save_supply_rules(rules), control=save_button, done=done)

        save_button = button(tr('modes.save_supply'), save_supply)
        save_button.set_halign(Gtk.Align.START)
        content.append(save_button)

    def refresh(self, state):
        cpu = state.get('cpu_epp', {})
        pref = tr('common.list_separator').join(cpu.get('energy_performance_preference', [])) or '--'
        governor = tr('common.list_separator').join(cpu.get('scaling_governor', [])) or '--'
        self.epp_live.set_label(tr('modes.epp_reading', pref=pref, governor=governor))
