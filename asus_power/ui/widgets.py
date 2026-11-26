"""Shared GTK construction and charging controls."""
from .gtk import Adw, Gtk

MODENAMES = {'quiet': '静音', 'balanced': '均衡', 'performance': '性能',
             'low-power': '节能', 'custom': '自定义'}
EPP_NAMES = ('固件默认', '性能优先', '均衡偏性能', '均衡偏省电', '省电优先')
FAN_NAMES = {'cpu': 'CPU 风扇', 'gpu': 'GPU 风扇', 'mid': '第三风扇'}


def box(horizontal=False, spacing=10):
    return Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL if horizontal else Gtk.Orientation.VERTICAL,
                   spacing=spacing)


def flow(maximum=3, homogeneous=False):
    widget = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=homogeneous,
                         row_spacing=8, column_spacing=12, min_children_per_line=1,
                         max_children_per_line=maximum, activate_on_single_click=False)
    widget.add_css_class('dashboard-flow')
    return widget


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
    view.set_child(Adw.Clamp(child=content, maximum_size=840, tightening_threshold=620))
    return view


def card(parent, title):
    result = box(spacing=12)
    result.add_css_class('dashboard-card')
    heading = label(title)
    heading.add_css_class('dashboard-heading')
    result.append(heading)
    if parent is not None:
        parent.append(result)
    return result


def fmt(value, unit=''):
    return '--' if value is None else f'{value:g}{unit}'


class ChargeControls:
    def __init__(self, controller, parent):
        self.controller = controller
        row = box(True)
        parent.append(row)
        self.entry = Gtk.Entry(text='--', width_chars=5)
        row.append(self.entry)
        row.append(label('%'))

        def apply():
            try:
                value = int(self.entry.get_text())
            except ValueError:
                self.controller.status.set_label('请输入 20–100 的整数')
                return
            self.controller.submit(lambda: self.controller.backend.set_limit(value))

        self.action = button('保存上限', apply)
        row.append(self.action)
        self.entry.set_sensitive(False)
        self.action.set_sensitive(False)

    def ready(self, caps):
        enabled = caps['charge_limit'] and caps['modern_cli']
        for widget in (self.entry, self.action):
            widget.set_sensitive(enabled)

    def refresh(self, value):
        if self.entry.get_text() == '--' and value is not None:
            self.entry.set_text(str(int(value)))
