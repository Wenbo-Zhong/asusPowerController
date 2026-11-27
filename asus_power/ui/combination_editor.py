"""功耗/风扇共用六套草稿；保存修改配置，立即应用只提交选中的硬件草稿。"""
from ..i18n import tr
from copy import deepcopy

from ..combinations import MODES, effective
from ..hardware.power_limits import attribute_unit
from .curve_editor import CurveEditor
from .drafts import CombinationDraft
from .gtk import Gtk
from .widgets import MODENAMES, box, button, card, confirm, dropdown, flow, label


class CombinationEditor:
    def __init__(self, controller):
        self.controller = controller
        self.model = CombinationDraft(controller.combinations)
        self.loading = False
        self.busy = False
        self.power_rows = {}
        self.header = card(None, tr('editor.title'))
        self.header.append(label(tr('editor.context_note'), True))
        selectors = flow(maximum=2, homogeneous=True)
        self.header.append(selectors)
        self.mode = dropdown([MODENAMES[mode] for mode in MODES])
        self.mode.set_selected(1)
        self.source = dropdown([tr('supply.ac'), tr('supply.battery')])
        for title, item in ((tr('editor.mode'), self.mode), (tr('editor.source'), self.source)):
            group = box(True)
            group.append(label(title, True))
            item.set_hexpand(True)
            group.append(item)
            selectors.insert(group, -1)
        actions = flow(maximum=2)
        self.header.append(actions)
        actions.insert(button(tr('editor.apply'), self.apply), -1)
        save_button = button(tr('editor.save'), self.save)
        save_button.add_css_class('suggested-action')
        actions.insert(save_button, -1)
        self.enabled = Gtk.CheckButton(label=tr('editor.auto'),
                                       active=self.model.settings['enabled'])
        self.header.append(self.enabled)
        self.live = label(tr('editor.live_unknown'), True)
        self.header.append(self.live)
        self.note = label('', True)
        self.header.append(self.note)
        for key, title in [('power', tr('editor.power_title')), ('fans', tr('editor.fans_title'))]:
            content = box()
            setattr(self, key + '_page', content)
            setattr(self, key + '_card', card(content, title))
        self.power_empty = label('--', True)
        self.power_card.append(self.power_empty)
        self.fan = CurveEditor(self.fans_card)
        fan_actions = flow()
        fan_actions.insert(button(tr('editor.read_fans'), self.read_curves), -1)
        fan_actions.insert(button(tr('editor.copy_fans'), self.copy_curves), -1)
        self.reset_fans = button(tr('editor.reset_fans'), self.restore_curves)
        self.reset_fans.set_sensitive(False)
        fan_actions.insert(self.reset_fans, -1)
        self.fans_card.append(fan_actions)
        for selector in (self.mode, self.source):
            selector.connect('notify::selected', lambda *_: self.switch())
        self.load_entry()

    def populate(self, caps):
        fan_info = caps.get('fan_curves', {})
        if fan_info.get('supported'):
            self.reset_fans.set_sensitive(True)
            self.model.templates[fan_info['initial_profile']] = deepcopy(fan_info['curves'])
        self.load_entry()
        self.refresh(self.controller.state)

    def add_power_row(self, key, data):
        group = box(spacing=6)
        self.power_card.append(group)
        checked = Gtk.CheckButton(label=data['label'])
        checked.set_hexpand(True)
        group.append(checked)
        row = flow(maximum=2)
        group.append(row)
        field = Gtk.SpinButton.new_with_range(data['minimum'], data['maximum'], data['step'])
        field.set_value(data['current'])
        value = box(True)
        value.append(field)
        value.append(label(attribute_unit(key)))
        row.insert(value, -1)
        default = button(tr('editor.fill_defaults'), lambda key=key: self.fill_default(key))
        row.insert(default, -1)
        reading = label('--', True)
        group.append(reading)
        self.power_rows[key] = checked, field, reading, default, data
        self.load_power_row(key)

    def load_power_row(self, key):
        check, field, _, _, data = self.power_rows[key]
        power = self.model.entry['power']
        check.set_active(key in power)
        value = power.get(key, data['current'])
        # 其他供电条件下保存的数值可能超出当前范围；先保留草稿，应用时再校验。
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
            self.note.set_label(tr('editor.saved'))

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
            notice=self.note, pending=tr('editor.applying'),
            finished=lambda: setattr(self, 'busy', False))

    def read_curves(self):
        if not self.controller.backend:
            return
        if self.fan.managed.get_active():
            self.note.set_label(tr('editor.unmanage_note'))
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
                self.note.set_label(tr('editor.fans_reset'))

            self.controller.perform(lambda: self.controller.backend.reset_fan_curves(mode),
                                    control=self.reset_fans, done=done, notice=self.note)

        confirm(self.controller.window, tr('editor.reset_confirm'),
                tr('editor.reset_note', mode=MODENAMES[mode]),
                tr('editor.reset_action'), apply)

    def copy_curves(self):
        try:
            curves = self.fan.export()
        except ValueError as error:
            self.note.set_label(str(error))
            return
        if not curves:
            self.note.set_label(tr('editor.read_and_manage_first'))
            return
        selected = self.model.other_supply()
        mode, source = selected

        def copy():
            self.model.replace_fans(selected, curves)
            self.note.set_label(tr('editor.copied'))

        if self.model.settings['entries'][mode][source]['fans']:
            confirm(self.controller.window, tr('editor.copy_confirm'), '', tr('editor.overwrite'), copy)
        else:
            copy()

    def refresh(self, state):
        mode, ac = state.get('profile'), state.get('ac')
        self.live.set_label(tr('editor.live', mode=MODENAMES.get(mode, mode) or '--',
                               source=tr('supply.ac') if ac is True else tr('supply.battery') if ac is False else '--'))
        power = state.get('power_limits') or self.controller.caps.get('power_limits', {})
        gpu_block = power.get('gpu_block')
        for key, data in power.get('attributes', {}).items():
            if key not in self.power_rows:
                self.add_power_row(key, data)
        self.power_empty.set_visible(bool(gpu_block) or not self.power_rows)
        self.power_empty.set_label(gpu_block or power.get('reason', tr('editor.no_power')))
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
            text = tr('editor.power_reading', current=actual['current'], unit=unit,
                      minimum=actual['minimum'], maximum=actual['maximum'])
            if actual.get('pending') is not None:
                text += tr('editor.pending_suffix', pending=actual['pending'], unit=unit)
            reading.set_label(text)
            default.set_sensitive(actual.get('default') is not None)
