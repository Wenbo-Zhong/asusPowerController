"""Supply-driven profile switching and independently saved asusd EPP rules."""
from ..combinations import MODES
from ..hardware.profile_epp import EPP
from .gtk import Gtk
from .widgets import EPP_NAMES, MODENAMES, box, button, card, dropdown, flow, label


class ModeSettings:
    def __init__(self, controller, page):
        self.controller = controller
        auto = card(page, '跟随供电切换运行方式')
        auto.append(label('本应用运行时生效，包括收进托盘后；每次插拔电源只切换一次。更改后自动保存。', True))
        self.auto = Gtk.CheckButton(label='开启自动切换', active=bool(controller.settings.get('enabled')))
        auto.append(self.auto)
        self.auto.connect('toggled', lambda *_: self.save_policy())
        self.policy_choices = []
        for text in ('插电', '离电'):
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
        self.supply_section = Gtk.Expander(label='asusd 插拔电源规则', visible=False)
        auto.append(self.supply_section)
        self.epp_card = card(page, '随运行方式切换 CPU EPP')
        self.epp_card.append(label('静音、均衡、性能各一条规则，插电和离电共用。独立保存到 asusd，退出本应用后仍生效。', True))
        self.epp_live = label('实际 EPP -- · 调速器 --', True)
        self.epp_card.append(self.epp_live)
        self.epp_linked = Gtk.CheckButton(label='随运行方式切换 EPP')
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
        self.epp_save = button('保存 EPP 规则', self.save_epp)
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
                self.epp_card.append(label('系统电源服务也可能修改实际 EPP。', True))
        else:
            self.epp_card.append(label(info.get('reason', 'asusd EPP 不可用'), True))

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
        content.append(label('由 asusd 在后台执行。启用任一规则后，本应用的自动切换会关闭。', True))
        rule_widgets = {}
        for source, title in [('ac', '插电'), ('battery', '离电')]:
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
                    c.status.set_label('请选择插电和离电模式')
                    return
                rules[source] = toggle.get_active(), c.mode_keys[index]

            def done(message):
                if any(enabled for enabled, _ in rules.values()):
                    self.auto.set_active(False)
                c.status.set_label(message)

            c.perform(lambda: c.backend.save_supply_rules(rules), control=save_button, done=done)

        save_button = button('保存供电规则', save_supply)
        save_button.set_halign(Gtk.Align.START)
        content.append(save_button)

    def refresh(self, state):
        cpu = state.get('cpu_epp', {})
        pref = '、'.join(cpu.get('energy_performance_preference', [])) or '--'
        governor = '、'.join(cpu.get('scaling_governor', [])) or '--'
        self.epp_live.set_label(f'实际 EPP {pref} · 调速器 {governor}')
