"""隔离 GI 依赖，记录真实页面构造、父子关系和信号；不模拟 GTK 的像素排版。"""
from contextlib import contextmanager
import importlib
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


class Widget:
    def __init__(self, **properties):
        self.props = dict(visible=True, sensitive=True, active=False, selected=0,
                          label='', text='', value=0)
        self.props.update(properties)
        self.children = []
        self.parent = None
        self.signals = {}
        self.css = set()
        self.pages = {}
        if properties.get('child') is not None:
            self.append(properties['child'])

    def connect(self, signal, callback):
        self.signals.setdefault(signal, []).append(callback)

    def emit(self, signal):
        for callback in self.signals.get(signal, []):
            callback(self)

    def append(self, child):
        if child.parent is not None:
            raise AssertionError('控件不能同时属于两个父容器')
        child.parent = self
        self.children.append(child)

    def insert(self, child, position):
        wrapper = Widget()
        wrapper.append(child)
        self.append(wrapper)

    def attach(self, child, *_):
        self.append(child)

    def set_child(self, child):
        for former in list(self.children):
            self.remove(former)
        if child is not None:
            self.append(child)

    set_content = set_child
    set_title_widget = append
    add_top_bar = append

    def remove(self, child):
        self.children.remove(child)
        child.parent = None

    def get_first_child(self):
        return self.children[0] if self.children else None

    def add_titled(self, child, name, title):
        self.append(child)
        self.pages[name] = (title, child)
        if 'visible_child_name' not in self.props:
            self.props['visible_child_name'] = name

    def add_css_class(self, name):
        self.css.add(name)

    def remove_css_class(self, name):
        self.css.discard(name)

    def __getattr__(self, name):
        setters = {'label', 'text', 'selected', 'active', 'value', 'model', 'visible',
                   'sensitive', 'hexpand', 'halign', 'xalign', 'homogeneous', 'width_chars',
                   'margin_start', 'margin_end', 'digits', 'policy', 'range', 'increments',
                   'draw_func', 'title', 'rgba', 'visible_child_name'}
        if name.startswith('set_') and name[4:] in setters:
            key = name[4:]

            def setter(value, *rest):
                previous = self.props.get(key)
                self.props[key] = (value, *rest) if rest else value
                if previous != value:
                    self.emit('toggled' if key == 'active' else 'notify::' + key.replace('_', '-'))
            return setter
        if name.startswith('get_') and name[4:] in setters:
            return lambda: self.props.get(name[4:])
        raise AttributeError(name)

    def get_value_as_int(self):
        return round(self.props['value'])

    def update(self):
        pass

    def queue_draw(self):
        pass

    def add_controller(self, gesture):
        self.props['gesture'] = gesture

    def load_from_data(self, data):
        self.props['data'] = data

    def present(self):
        self.props['visible'] = True

    @classmethod
    def new_from_strings(cls, items):
        return cls(model=list(items))

    @classmethod
    def new_with_range(cls, minimum, maximum, step):
        return cls(value=minimum, range=(minimum, maximum), increments=(step, step))

    @classmethod
    def new(cls, text, length):
        return cls(text=text[:length])

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


class Color:
    def parse(self, text):
        self.red, self.green, self.blue = (int(text[i:i + 2], 16) / 255 for i in (1, 3, 5))


@contextmanager
def gtk_controller_module():
    gi = ModuleType('gi')
    gi.require_version = lambda *_: None
    gi.require_foreign = lambda *_: None
    repository = ModuleType('gi.repository')
    gtk = SimpleNamespace(**{name: type(name, (Widget,), {}) for name in (
        'Box', 'Label', 'Button', 'CheckButton', 'DropDown', 'Expander', 'ColorButton',
        'LinkButton', 'Stack', 'StackSwitcher', 'Entry', 'EntryBuffer', 'SpinButton',
        'DrawingArea', 'GestureDrag', 'Grid', 'FlowBox', 'ScrolledWindow', 'CssProvider')})
    gtk.Orientation = SimpleNamespace(HORIZONTAL='horizontal', VERTICAL='vertical')
    gtk.Align = SimpleNamespace(START='start', END='end', CENTER='center')
    gtk.SelectionMode = SimpleNamespace(NONE='none')
    gtk.PolicyType = SimpleNamespace(NEVER='never', AUTOMATIC='automatic')
    gtk.StringList = SimpleNamespace(new=lambda values: list(values))
    gtk.INVALID_LIST_POSITION = 2 ** 32 - 1
    gtk.STYLE_PROVIDER_PRIORITY_APPLICATION = 600
    gtk.StyleContext = SimpleNamespace(add_provider_for_display=lambda *_: None)
    repository.Gtk = gtk
    repository.Adw = SimpleNamespace(**{name: type(name, (Widget,), {}) for name in (
        'ApplicationWindow', 'ToolbarView', 'HeaderBar', 'WindowTitle', 'Clamp')})
    repository.Gdk = SimpleNamespace(RGBA=Color, Display=SimpleNamespace(get_default=lambda: None))
    repository.Gio = SimpleNamespace()
    repository.GLib = SimpleNamespace(SOURCE_REMOVE=False, idle_add=Mock(), timeout_add=Mock())
    with patch.dict(sys.modules, {'gi': gi, 'gi.repository': repository}):
        # 不让其他用例已经导入的 GI 替身影响本次页面构造。
        for name in list(sys.modules):
            if name in ('asus_power.desktop_loop',) or (
                    name.startswith('asus_power.ui.') and name != 'asus_power.ui.drafts'):
                del sys.modules[name]
        yield importlib.import_module('asus_power.ui.controller')
