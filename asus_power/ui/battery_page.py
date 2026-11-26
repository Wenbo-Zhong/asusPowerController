"""Battery information and one-shot charging."""
from ..hardware.battery import formatted_values
from .gtk import Gtk
from .widgets import box, button, card, confirm, label


class BatteryPage:
    def __init__(self, controller, page, charge):
        self.controller = controller
        summary = card(page, '电池状态')
        self.summary = label('--', True)
        summary.append(self.summary)
        metrics = box(True, 12)
        metrics.set_homogeneous(True)
        summary.append(metrics)
        self.metrics = {}
        for key, title in [('percent', '当前电量'), ('health', '健康度'),
                           ('power', '电池功率'), ('time', '预计时间')]:
            metric = box(spacing=4)
            caption, value = label(title, True), label()
            value.add_css_class('dashboard-metric')
            metric.append(caption)
            metric.append(value)
            metrics.append(metric)
            self.metrics[key] = caption, value
        self.note = label('', True)
        summary.append(self.note)
        self.values = {}
        grids = box(True, 24)
        grids.set_homogeneous(True)
        card(page, '容量与设备信息').append(grids)
        groups = [
            [('当前能量', 'energy'), ('满充能量', 'full'), ('设计能量', 'design'),
             ('满充电荷量', 'charge_full'), ('设计电荷量', 'charge_design')],
            [('当前电压', 'voltage'), ('充放电电流', 'current'), ('制造商', 'manufacturer'),
             ('型号', 'model'), ('电池类型', 'technology')]]
        for group in groups:
            column = box()
            grids.append(column)
            for name, key in group:
                row = box(True)
                caption = label(name, True)
                caption.set_hexpand(True)
                row.append(caption)
                value = label()
                value.set_xalign(1)
                value.add_css_class('dashboard-value')
                row.append(value)
                column.append(row)
                self.values[key] = value
        charging = card(page, '充电上限')
        charge.append(charging)
        self.full_charge = button('一次充满', self.one_shot_charge)
        self.full_charge.set_sensitive(False)
        self.full_charge.set_halign(Gtk.Align.START)
        charging.append(self.full_charge)

    def ready(self, caps):
        self.full_charge.set_sensitive(caps['charge_limit'])

    def refresh(self, state):
        battery = formatted_values(state.get('battery_info') or {})
        self.summary.set_label(battery['summary'])
        self.note.set_label(battery['note'])
        for key, (caption, value) in self.metrics.items():
            if key in ('power', 'time'):
                caption.set_label(battery[f'{key}_title'])
            value.set_label(battery[key])
        for key, widget in self.values.items():
            widget.set_label(battery.get(key, '--'))

    def one_shot_charge(self):
        def apply():
            self.controller.perform(self.controller.backend.one_shot_charge,
                                    control=self.full_charge)
        confirm(self.controller.window, '这次充电充至 100%？',
                'asusd 会临时提高充电上限，并负责恢复原上限。', '一次充满', apply)
