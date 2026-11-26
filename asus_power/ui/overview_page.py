"""Current computer status and immediate operating-mode shortcuts."""
from ..combinations import MODES
from .widgets import MODENAMES, box, button, card, flow, fmt, label


class OverviewPage:
    def __init__(self, controller, page):
        self.controller = controller
        summary = card(page, '电脑状态')
        indicators = flow(homogeneous=True)
        summary.append(indicators)
        self.values = {}
        for key, title in [('charge', '当前电量'), ('source', '供电'), ('mode', '运行方式')]:
            metric = box(spacing=4)
            metric.append(label(title, True))
            value = label()
            value.add_css_class('dashboard-metric')
            metric.append(value)
            indicators.insert(metric, -1)
            self.values[key] = value
        self.metrics = label('充电上限 -- · 电池功率 --', True)
        summary.append(self.metrics)
        modes = card(page, '切换运行方式')
        modes.append(label('点击后立即切换；当前运行方式以实际读数为准。', True))
        self.mode_row = flow(homogeneous=True)
        modes.append(self.mode_row)
        self.mode_buttons = {}
        for key in MODES:
            self.add_mode(key)
        shortcuts = card(page, '常用入口')
        actions = flow(maximum=2, homogeneous=True)
        shortcuts.append(actions)
        actions.insert(button('性能、温度与风扇',
                              lambda: controller.stack.set_visible_child_name('performance')), -1)
        actions.insert(button('电池与充电上限',
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
        self.values['source'].set_label('外接电源' if ac is True else '电池供电' if ac is False else '--')
        mode = state['profile']
        self.values['mode'].set_label(MODENAMES.get(mode, mode) or '--')
        for key, widget in self.mode_buttons.items():
            if key == mode:
                widget.add_css_class('suggested-action')
            else:
                widget.remove_css_class('suggested-action')
        self.metrics.set_label(f"充电上限 {fmt(state['limit'], '%')} · 电池功率 {fmt(state['battery_watts'], ' W')}")
