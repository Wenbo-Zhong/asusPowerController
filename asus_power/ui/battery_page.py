"""电池页：显示 battery 格式化遥测，提供充电上限和一次充满操作。"""
from ..i18n import tr
from ..hardware.battery import formatted_values
from .gtk import Gtk
from .widgets import ChargeControls, box, button, card, confirm, flow, label


class BatteryPage:
    def __init__(self, controller, page):
        self.controller = controller
        summary = card(page, tr('battery.status_title'))
        self.summary = label('--', True)
        summary.append(self.summary)
        metrics = flow(maximum=4, homogeneous=True)
        summary.append(metrics)
        self.metrics = {}
        for key, title in [('percent', tr('battery.level')), ('health', tr('battery.health')),
                           ('power', tr('battery.power')), ('time', tr('battery.estimated_time'))]:
            metric = box(spacing=4)
            caption, value = label(title, True), label()
            value.add_css_class('dashboard-metric')
            metric.append(caption)
            metric.append(value)
            metrics.insert(metric, -1)
            self.metrics[key] = caption, value
        self.note = label('', True)
        summary.append(self.note)
        charging = card(page, tr('battery.charge_limit'))
        self.charge = ChargeControls(controller, charging)
        self.full_charge = button(tr('battery.full_charge'), self.one_shot_charge)
        self.full_charge.set_sensitive(False)
        self.full_charge.set_halign(Gtk.Align.START)
        charging.append(self.full_charge)
        self.values = {}
        groups = [
            [(tr('battery.energy_now'), 'energy'), (tr('battery.energy_full'), 'full'), (tr('battery.energy_design'), 'design'),
             (tr('battery.charge_full'), 'charge_full'), (tr('battery.charge_design'), 'charge_design')],
            [(tr('battery.voltage'), 'voltage'), (tr('battery.current'), 'current'), (tr('battery.manufacturer'), 'manufacturer'),
             (tr('battery.model'), 'model'), (tr('battery.technology'), 'technology')]]
        for title, group in zip((tr('battery.capacity_title'), tr('battery.device_title')), groups):
            column = card(page, title)
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

    def ready(self, caps):
        self.charge.ready(caps)
        self.full_charge.set_sensitive(caps['charge_limit'])

    def refresh(self, state):
        self.charge.refresh(state['limit'])
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
        confirm(self.controller.window, tr('battery.full_charge_confirm'),
                tr('battery.full_charge_note'), tr('battery.full_charge'), apply)
