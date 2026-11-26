"""GTK editor for six supply/profile drafts and asusd EPP rules."""
from copy import deepcopy

from ..combinations import MODES, effective
from ..hardware.power_limits import attribute_unit
from ..hardware.profile_epp import EPP
from .curve_editor import CurveEditor
from .drafts import CombinationDraft
from .gtk import Gtk
from .widgets import EPP_NAMES, MODENAMES, box, button, card, confirm, dropdown, label, scroll


class CombinationEditor:
    def __init__(self, controller, page):
        self.controller = controller
        self.model = CombinationDraft(controller.combinations)
        self.loading = False
        self.busy = False
        self.power_rows = {}
        self.epp_choices = []
        header = card(page, '编辑组合')
        row = box(True)
        header.append(row)
        self.mode = dropdown(['省电', '均衡', '性能'])
        self.mode.set_selected(1)
        self.source = dropdown(['插电', '离电'])
        for item in (self.mode, self.source):
            row.append(item)
        spacer = box()
        spacer.set_hexpand(True)
        row.append(spacer)
        row.append(button('应用此组合', self.apply))
        save_button = button('保存全部配置', self.save)
        save_button.add_css_class('suggested-action')
        row.append(save_button)
        self.enabled = Gtk.CheckButton(label='自动联动（保存后生效）', active=self.model.settings['enabled'])
        state_row = box(True)
        header.append(state_row)
        self.enabled.set_hexpand(True)
        state_row.append(self.enabled)
        self.live = label('当前组合 --', True)
        state_row.append(self.live)
        self.note = label('', True)
        header.append(self.note)
        self.sections = Gtk.Stack(vexpand=True, hhomogeneous=False)
        page.append(Gtk.StackSwitcher(stack=self.sections, halign=Gtk.Align.CENTER))
        page.append(self.sections)
        for key, title in [('epp', 'EPP'), ('power', '功耗'), ('fans', '风扇')]:
            content = box()
            self.sections.add_titled(scroll(content), key, title)
            setattr(self, key + '_card', card(content, {'epp': 'asusd 模式联动',
                                                       'power': '此组合的功耗',
                                                       'fans': '此组合的风扇曲线'}[key]))
        self.epp_live = label('实际 EPP -- · 调速器 --', True)
        self.epp_card.append(self.epp_live)
        self.epp_linked = Gtk.CheckButton(label='随方案切换 EPP')
        self.epp_card.append(self.epp_linked)
        columns = box(True)
        columns.set_homogeneous(True)
        self.epp_card.append(columns)
        for mode in MODES:
            group = box()
            columns.append(group)
            group.append(label(MODENAMES[mode]))
            choice = dropdown(EPP_NAMES)
            group.append(choice)
            self.epp_choices.append(choice)
        self.epp_save = button('保存 asusd 规则', self.save_epp)
        self.epp_card.append(self.epp_save)
        self.epp_card.set_sensitive(False)
        self.power_empty = label('--', True)
        self.power_card.append(self.power_empty)
        self.fan = CurveEditor(self.fans_card)
        fan_actions = box(True)
        fan_actions.append(button('读取方案曲线', self.read_curves))
        fan_actions.append(button('复制到另一种供电', self.copy_curves))
        self.reset_fans = button('恢复原厂曲线', self.restore_curves)
        self.reset_fans.set_sensitive(False)
        fan_actions.append(self.reset_fans)
        self.fans_card.append(fan_actions)
        for selector in (self.mode, self.source):
            selector.connect('notify::selected', lambda *_: self.switch())
        self.load_entry()

    def populate(self, caps):
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
        fan_info = caps.get('fan_curves', {})
        if fan_info.get('supported'):
            self.reset_fans.set_sensitive(True)
            self.model.templates[fan_info['initial_profile']] = deepcopy(fan_info['curves'])
        self.load_entry()
        self.refresh(self.controller.state)

    def add_power_row(self, key, data):
        row = box(True)
        self.power_card.append(row)
        checked = Gtk.CheckButton(label=data['label'])
        checked.set_hexpand(True)
        row.append(checked)
        field = Gtk.SpinButton.new_with_range(data['minimum'], data['maximum'], data['step'])
        field.set_value(data['current'])
        row.append(field)
        row.append(label(attribute_unit(key)))
        default = button('填入默认值', lambda key=key: self.fill_default(key))
        row.append(default)
        reading = label('--', True)
        self.power_card.append(reading)
        self.power_rows[key] = checked, field, reading, default, data
        self.load_power_row(key)

    def load_power_row(self, key):
        check, field, _, _, data = self.power_rows[key]
        power = self.model.entry['power']
        check.set_active(key in power)
        value = power.get(key, data['current'])
        # Preserve saved values from a different power-source range.
        field.set_range(min(value, data['minimum']), max(value, data['maximum']))
        field.set_increments(data['step'], data['step'])
        field.set_value(value)

    def fill_default(self, key):
        check, field, _, _, data = self.power_rows[key]
        if data.get('default') is not None:
            field.set_value(data['default'])
            check.set_active(True)

    def collect(self):
        values = {}
        for key, (check, field, _, _, _) in self.power_rows.items():
            if check.get_active():
                field.update()
                values[key] = field.get_value_as_int()
            else:
                values[key] = None
        self.model.collect(values, self.fan.export(), self.enabled.get_active())

    def load_entry(self):
        for key in self.power_rows:
            self.load_power_row(key)
        self.fan.load(self.model.curves(), bool(self.model.entry['fans']))

    def switch(self):
        if self.loading:
            return
        try:
            self.collect()
        except ValueError as error:
            self.note.set_label(str(error))
            self.loading = True
            self.mode.set_selected(MODES.index(self.model.selected[0]))
            self.source.set_selected(int(self.model.selected[1] == 'battery'))
            self.loading = False
            return
        self.model.select(MODES[self.mode.get_selected()], ('ac', 'battery')[self.source.get_selected()])
        self.load_entry()

    def save(self):
        try:
            self.collect()
            settings = self.model.export()
        except ValueError as error:
            self.note.set_label(str(error))
            return
        if self.controller.persist(combinations=settings):
            self.controller.combinations = settings
            self.controller.combination_policy.invalidate()
            self.note.set_label('六套组合已保存')

    def apply(self):
        if not self.controller.backend or self.busy:
            return
        try:
            self.collect()
            entry = effective(self.model.settings, *self.model.selected)
        except ValueError as error:
            self.note.set_label(str(error))
            return
        mode, source = self.model.selected
        self.busy = True
        self.controller.perform(
            lambda: self.controller.backend.apply_combination(mode, source, entry),
            notice=self.note, pending='正在应用组合…',
            finished=lambda: setattr(self, 'busy', False))

    def save_epp(self):
        profiles = {mode: EPP[choice.get_selected()] for mode, choice in zip(MODES, self.epp_choices)}
        linked = self.epp_linked.get_active()
        self.controller.perform(lambda: self.controller.backend.save_profile_epp(linked, profiles),
                                control=self.epp_save)

    def read_curves(self):
        if not self.controller.backend:
            return
        if self.fan.managed.get_active():
            self.note.set_label('取消勾选「管理风扇曲线」后可重新读取方案曲线。')
            return
        selected = self.model.selected

        def done(curves):
            self.model.templates[selected[0]] = deepcopy(curves)
            if self.model.selected == selected and not self.fan.managed.get_active():
                self.fan.load(curves, False)

        self.controller.submit(lambda: self.controller.backend.read_fan_curves(selected[0]), done)

    def restore_curves(self):
        selected = self.model.selected
        mode, _ = selected

        def apply():
            def done(curves):
                self.model.templates[mode] = deepcopy(curves)
                self.model.replace_fans(selected, curves)
                if self.model.selected == selected:
                    self.fan.load(curves, True)
                self.note.set_label('asusd 曲线已恢复。保存全部配置可更新当前组合。')

            self.controller.perform(lambda: self.controller.backend.reset_fan_curves(mode),
                                    control=self.reset_fans, done=done, notice=self.note)

        confirm(self.controller.window, '恢复原厂风扇曲线？',
                f'这会重置 asusd「{MODENAMES[mode]}」的所有风扇，并替换当前组合的曲线草稿。',
                '恢复原厂', apply)

    def copy_curves(self):
        try:
            curves = self.fan.export()
        except ValueError as error:
            self.note.set_label(str(error))
            return
        if not curves:
            self.note.set_label('请先读取曲线并启用风扇管理')
            return
        selected = self.model.other_supply()
        mode, source = selected

        def copy():
            self.model.replace_fans(selected, curves)
            self.note.set_label('已复制到另一种供电的草稿')

        if self.model.settings['entries'][mode][source]['fans']:
            confirm(self.controller.window, '覆盖另一种供电的曲线？', '', '覆盖', copy)
        else:
            copy()

    def refresh(self, state):
        mode, ac = state.get('profile'), state.get('ac')
        self.live.set_label(f"当前组合 {MODENAMES.get(mode, '--')} · "
                            + ('插电' if ac is True else '离电' if ac is False else '--'))
        cpu = state.get('cpu_epp', {})
        pref = '、'.join(cpu.get('energy_performance_preference', [])) or '--'
        governor = '、'.join(cpu.get('scaling_governor', [])) or '--'
        self.epp_live.set_label(f'实际 EPP {pref} · 调速器 {governor}')
        power = state.get('power_limits') or self.controller.caps.get('power_limits', {})
        gpu_block = power.get('gpu_block')
        for key, data in power.get('attributes', {}).items():
            if key not in self.power_rows:
                self.add_power_row(key, data)
        self.power_empty.set_visible(bool(gpu_block) or not self.power_rows)
        self.power_empty.set_label(gpu_block or power.get('reason', '未检测到可调功耗'))
        for key, (check, field, reading, default, data) in self.power_rows.items():
            if key.startswith('nv_'):
                check.set_sensitive(not gpu_block)
                field.set_sensitive(not gpu_block)
                default.set_sensitive(not gpu_block and data.get('default') is not None)
                if gpu_block:
                    reading.set_label(gpu_block)
        for key, actual in power.get('attributes', {}).items():
            if key not in self.power_rows:
                continue
            check, field, reading, default, former = self.power_rows[key]
            if actual != former:
                self.power_rows[key] = check, field, reading, default, actual
            unit = attribute_unit(key)
            text = f"实际 {actual['current']} {unit} · 范围 {actual['minimum']}–{actual['maximum']} {unit}"
            if actual.get('pending') is not None:
                text += f" · 待应用 {actual['pending']} {unit}"
            reading.set_label(text)
            default.set_sensitive(actual.get('default') is not None)
