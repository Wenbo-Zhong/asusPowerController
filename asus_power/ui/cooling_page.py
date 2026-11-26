"""Temperature, fan telemetry and validated APU controls."""
from .gtk import Gtk
from .widgets import box, button, card, flow, fmt, label


class CoolingPage:
    def __init__(self, controller, page):
        self.controller = controller
        readings = card(page, '实时温度与转速')
        columns = flow(maximum=2, homogeneous=True)
        readings.append(columns)
        self.sensor_columns = {}
        self.sensor_rows = {'temperature': {}, 'fan': {}}
        for key, heading in [('temperature', '温度'), ('fan', '风扇转速')]:
            column = box(spacing=6)
            columns.insert(column, -1)
            column.append(label(heading, True))
            values = box(spacing=6)
            values.append(label())
            column.append(values)
            self.sensor_columns[key] = values
        shortcut = button('编辑风扇曲线', self.show_fans)
        shortcut.set_halign(Gtk.Align.START)
        readings.append(shortcut)
        gpu = card(page, '核显最高频率')
        self.gpu_status = label()
        gpu.append(self.gpu_status)
        row = box(True)
        gpu.append(row)
        self.gpu_target = Gtk.Entry(text='--', width_chars=7)
        row.append(self.gpu_target)
        row.append(label('MHz'))
        actions = flow()
        gpu.append(actions)
        self.gpu_apply = button('应用', self.apply_gpu)
        self.gpu_reset = button('恢复自动调频', lambda: self.gpu_operation(controller.backend.set_igpu_max))
        self.gpu_install = button('安装控制组件', lambda: self.gpu_operation(controller.backend.install_igpu_helper))
        for widget in (self.gpu_apply, self.gpu_reset, self.gpu_install):
            actions.insert(widget, -1)
            widget.set_sensitive(False)
        self.gpu_target.set_sensitive(False)
        self.gpu_busy = False

    def ready(self, caps):
        self.refresh_gpu(caps.get('igpu_clock', {}))

    def refresh(self, state):
        groups = {'temperature': {}, 'fan': {}}
        for name, value in state['sensors'].items():
            is_fan = name.endswith(' RPM')
            group = groups['fan' if is_fan else 'temperature']
            group[name.removesuffix(' RPM') if is_fan else name] = fmt(value, ' RPM' if is_fan else ' °C')
        for key, values in groups.items():
            column, rows = self.sensor_columns[key], self.sensor_rows[key]
            if rows.keys() != values.keys():
                while child := column.get_first_child():
                    column.remove(child)
                rows.clear()
                for name in values:
                    row = box(True)
                    caption, number = label(name, True), label()
                    caption.set_hexpand(True)
                    number.set_xalign(1)
                    number.add_css_class('dashboard-value')
                    row.append(caption)
                    row.append(number)
                    column.append(row)
                    rows[name] = number
                if not rows:
                    column.append(label())
            for name, text in values.items():
                rows[name].set_label(text)
        self.refresh_gpu(state.get('igpu_clock', {}))

    def show_fans(self):
        self.controller.performance.show_fans()

    def apply_gpu(self):
        try:
            value = int(self.gpu_target.get_text())
        except ValueError:
            self.controller.status.set_label('请输入整数 MHz')
            return
        self.gpu_operation(lambda: self.controller.backend.set_igpu_max(value))

    def gpu_operation(self, command):
        if self.gpu_busy:
            return
        self.gpu_busy = True
        c = self.controller
        self.refresh_gpu(c.state.get('igpu_clock', c.caps.get('igpu_clock', {})))

        def complete():
            self.gpu_busy = False
            c.submit(c.backend.igpu_clock, lambda info: self.refresh_gpu(info, update_input=True))

        c.submit(command, finished=complete)

    def refresh_gpu(self, info, update_input=False):
        supported = bool(info.get('supported'))
        ready = supported and info.get('helper_ready')
        self.gpu_install.set_visible(supported and not ready)
        self.gpu_install.set_sensitive(supported and not self.gpu_busy)
        for widget in (self.gpu_apply, self.gpu_reset, self.gpu_target):
            widget.set_sensitive(bool(ready and not self.gpu_busy))
        if supported:
            self.gpu_status.set_label(f"当前 {fmt(info.get('frequency_mhz'), ' MHz')} · 上限 {info['maximum']} MHz"
                                      f" · {info['mode']} · 范围 {info['minimum']}–{info['upper']} MHz")
            if update_input or self.gpu_target.get_text() == '--':
                self.gpu_target.set_text(str(info['maximum']))
        else:
            self.gpu_status.set_label(info.get('reason', '--'))
