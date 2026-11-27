"""概览页：呈现实时状态与立即切换模式入口，不保存组合草稿。"""
from ..i18n import tr
from ..combinations import MODES
from .widgets import MODENAMES, box, button, card, flow, fmt, label


class OverviewPage:
    def __init__(self, controller, page):
        self.controller = controller
        summary = card(page, tr('overview.status_title'))
        indicators = flow(homogeneous=True)
        summary.append(indicators)
        self.values = {}
        for key, title in [('charge', tr('battery.level')), ('source', tr('editor.source')), ('mode', tr('editor.mode'))]:
            metric = box(spacing=4)
            metric.append(label(title, True))
            value = label()
            value.add_css_class('dashboard-metric')
            metric.append(value)
            indicators.insert(metric, -1)
            self.values[key] = value
        self.metrics = label(tr('overview.battery_unknown'), True)
        summary.append(self.metrics)
        modes = card(page, tr('overview.switch_title'))
        modes.append(label(tr('overview.switch_note'), True))
        self.mode_row = flow(homogeneous=True)
        modes.append(self.mode_row)
        self.mode_buttons = {}
        for key in MODES:
            self.add_mode(key)
        shortcuts = card(page, tr('overview.shortcuts'))
        actions = flow(maximum=2, homogeneous=True)
        shortcuts.append(actions)
        actions.insert(button(tr('overview.performance'),
                              lambda: controller.stack.set_visible_child_name('performance')), -1)
        actions.insert(button(tr('overview.battery'),
                              lambda: controller.stack.set_visible_child_name('battery')), -1)

    def add_mode(self, key):
        widget = button(MODENAMES.get(key, key),
                        lambda: self.controller.submit(lambda: self.controller.backend.set_profile(key)))
        widget.set_sensitive(False)
        self.mode_row.insert(widget, -1)
        self.mode_buttons[key] = widget

    def ready(self, caps):
        modes = self.controller.mode_keys
        for key in modes:
            if key not in self.mode_buttons:
                self.add_mode(key)
        for key, widget in self.mode_buttons.items():
            widget.set_visible(key in modes)
            widget.set_sensitive(caps['modern_cli'])

    def refresh(self, state):
        ac = state['ac']
        self.values['charge'].set_label(fmt(state['battery'], '%'))
        self.values['source'].set_label(tr('overview.ac') if ac is True else tr('overview.on_battery') if ac is False else '--')
        mode = state['profile']
        self.values['mode'].set_label(MODENAMES.get(mode, mode) or '--')
        for key, widget in self.mode_buttons.items():
            if key == mode:
                widget.add_css_class('suggested-action')
            else:
                widget.remove_css_class('suggested-action')
        self.metrics.set_label(tr('overview.battery_reading', limit=fmt(state['limit'], '%'), battery_watts=fmt(state['battery_watts'], ' W')))
