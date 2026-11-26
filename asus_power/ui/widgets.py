"""Shared GTK construction and charging controls."""
from .gtk import Adw, Gtk

MODENAMES = {'quiet': '静音', 'balanced': '均衡', 'performance': '性能',
             'low-power': '节能', 'custom': '自定义'}
EPP_NAMES = ('固件默认', '性能优先', '均衡偏性能', '均衡偏省电', '省电优先')
FAN_NAMES = {'cpu': 'CPU 风扇', 'gpu': 'GPU 风扇', 'mid': '第三风扇'}


def box(horizontal=False, spacing=10):
    return Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL if horizontal else Gtk.Orientation.VERTICAL,
                   spacing=spacing)


def label(text='--', subdued=False):
    widget = Gtk.Label(label=text, xalign=0, wrap=True)
    if subdued:
        widget.add_css_class('dim-label')
    return widget


def button(text, callback):
    widget = Gtk.Button(label=text)
    widget.connect('clicked', lambda *_: callback())
    return widget


def confirm(window, title, body, action, callback):
    dialog = Adw.MessageDialog(transient_for=window, modal=True, heading=title, body=body)
    dialog.add_response('cancel', '取消')
    dialog.add_response('apply', action)
    dialog.set_close_response('cancel')
    dialog.connect('response::apply', lambda *_: callback())
    dialog.present()


def dropdown(items):
    return Gtk.DropDown.new_from_strings(list(items))


def scroll(content):
    view = Gtk.ScrolledWindow(vexpand=True)
    view.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    view.set_child(content)
    return view


def card(parent, title):
    result = box(spacing=12)
    result.add_css_class('dashboard-card')
    heading = label(title)
    heading.add_css_class('dashboard-heading')
    result.append(heading)
    parent.append(result)
    return result


def fmt(value, unit=''):
    return '--' if value is None else f'{value:g}{unit}'


class ChargeControls:
    def __init__(self, controller):
        self.controller = controller
        self.buffer = Gtk.EntryBuffer.new('--', 2)
        self.controls = []

    def append(self, parent):
        row = box(True)
        parent.append(row)
        entry = Gtk.Entry(buffer=self.buffer, width_chars=5)
        row.append(entry)
        row.append(label('%'))

        def apply():
            try:
                value = int(entry.get_text())
            except ValueError:
                self.controller.status.set_label('请输入 20–100 的整数')
                return
            self.controller.submit(lambda: self.controller.backend.set_limit(value))

        action = button('保存上限', apply)
        row.append(action)
        entry.set_sensitive(False)
        action.set_sensitive(False)
        self.controls.append((entry, action))

    def ready(self, caps):
        enabled = caps['charge_limit'] and caps['modern_cli']
        for pair in self.controls:
            for widget in pair:
                widget.set_sensitive(enabled)

    def refresh(self, value):
        if self.buffer.get_text() == '--' and value is not None:
            text = str(int(value))
            self.buffer.set_text(text, len(text))
