"""Native GTK 4 desktop interface."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
import subprocess

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_foreign('cairo')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import autostart, backend_install
from .backend import LinuxBackend
from .battery import formatted_values
from .combinations import CombinationPolicy, MODES, effective, normalized
from .config import Policy, load, save
from .desktop_loop import DesktopLoop, once
from .fan_curves import validate as validate_curve
from .firmware import bios_info, check_bios_updates
from .keyboard_idle import KeyboardIdle
from .instance import Instance
from .profile_epp import EPP
from .power_limits import attribute_unit
from .rog_features import POWER_FIELDS
from .tray import SessionTray

MODENAMES = {'quiet': '静音', 'balanced': '均衡', 'performance': '性能',
             'low-power': '节能', 'custom': '自定义'}
EPP_NAMES = ('固件默认', '性能优先', '均衡偏性能', '均衡偏省电', '省电优先')
FAN_NAMES = {'cpu': 'CPU 风扇', 'gpu': 'GPU 风扇', 'mid': '第三风扇'}
LIGHT_TIMEOUTS = (0, 15, 30, 60, 120, 300)


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


class Controller:
    def __init__(self, app, instance, start_hidden):
        self.app = app
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.firmware_pool = ThreadPoolExecutor(max_workers=1)
        self.keyboard_idle = None
        self.keyboard_busy = False
        self.backend = None
        self.caps = {}
        self.state = {}
        self.policy = Policy()
        self.combination_policy = CombinationPolicy()
        self.settings = load()
        self.closing = False
        self.tray = None
        self.window = Adw.ApplicationWindow(application=app, title='电脑管家', default_width=900,
                                            default_height=740, icon_name='preferences-system-power')
        self.loop = DesktopLoop(lambda: 'normal' if self.window.get_visible() else 'withdrawn')
        self.window.connect('close-request', self.close_window)
        instance.attach(self.loop, self.window.present, self.quit)
        self.preset_error = None
        try:
            self.combinations = normalized(self.settings.get('combinations'))
        except ValueError as error:
            self.combinations = normalized(None)
            self.preset_error = str(error)

        css = Gtk.CssProvider()
        css.load_from_data(b"""
            .dashboard-card { background: @card_bg_color; border-radius: 16px; padding: 18px; }
            .dashboard-heading { font-weight: 700; font-size: 1.08em; }
            .dashboard-page { padding: 12px 24px 18px; }
            .dashboard-nav { padding: 14px 24px 4px; }
            .dashboard-nav button { border-radius: 12px; padding: 5px 15px; }
            .dashboard-metric { font-weight: 700; font-size: 1.3em; }
            .dashboard-value { font-feature-settings: "tnum"; }
            .dashboard-status { padding: 6px 24px 12px; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.stack = Gtk.Stack(vexpand=True, hhomogeneous=False)
        header.set_title_widget(Adw.WindowTitle(title='电脑管家'))
        toolbar.add_top_bar(header)
        content = box(spacing=0)
        toolbar.set_content(content)
        self.window.set_content(toolbar)
        navigation = box(True)
        navigation.add_css_class('dashboard-nav')
        switcher = Gtk.StackSwitcher(stack=self.stack, halign=Gtk.Align.START)
        navigation.append(switcher)
        content.append(navigation)
        content.append(self.stack)
        self.status = label('正在读取硬件…', subdued=True)
        self.status.add_css_class('dashboard-status')
        content.append(self.status)
        self.pages = {}
        for name, title in [('overview', '日常设置'), ('cooling', '散热调节'),
                            ('battery', '电池信息'), ('combination', '组合策略')]:
            page = box()
            page.add_css_class('dashboard-page')
            self.pages[name] = page
            if name == 'combination':
                self.stack.add_titled(page, name, title)
            else:
                self.stack.add_titled(scroll(page), name, title)
        self.build_overview()
        self.build_cooling()
        self.build_battery()
        self.combination = CombinationEditor(self, self.pages['combination'])
        if self.preset_error:
            self.combination.note.set_label('旧组合无法读取：' + self.preset_error)
        if not start_hidden:
            self.window.present()
        self.submit(self.detect, self.ready)
        GLib.idle_add(once(lambda: self.start_tray(start_hidden)))
        self.loop.after(1000, self.tick_keyboard)

    def start_tray(self, hidden):
        try:
            uid = int(os.environ.get('PKEXEC_UID', os.getuid())) if os.geteuid() == 0 else os.getuid()
            self.tray = SessionTray(self.loop, self.window.present, self.quit, uid, isolated=True)
        except (OSError, ValueError):
            pass
        if hidden:
            self.loop.after(2500 if self.tray else 0, self.show_without_tray)

    def show_without_tray(self):
        if not self.closing and (not self.tray or not self.tray.connected):
            self.window.present()

    def close_window(self, *_):
        if self.tray and self.tray.connected:
            self.window.set_visible(False)
        else:
            self.quit()
        return True

    def quit(self):
        if self.closing:
            return
        self.closing = True
        if self.tray:
            self.tray.close()
        self.window.destroy()
        self.app.quit()

    def submit(self, operation, done=None, finished=None, executor=None):
        future = (executor or self.pool).submit(operation)
        def receive():
            if self.closing:
                return
            try:
                result = future.result()
                if done:
                    done(result)
                elif isinstance(result, str):
                    self.status.set_label(result)
            except Exception as error:
                self.status.set_label(f'失败：{error}')
            finally:
                if finished:
                    finished()
        future.add_done_callback(lambda _: GLib.idle_add(once(receive)))

    @staticmethod
    def detect():
        backend = LinuxBackend()
        return backend, backend.capabilities()

    def build_overview(self):
        page = self.pages['overview']
        rows = []
        for _ in range(3):
            row = box(True, 14)
            row.set_homogeneous(True)
            page.append(row)
            rows.append(row)
        summary = card(rows[0], '今天的电脑状态')
        indicators = box(True, 8)
        indicators.set_homogeneous(True)
        summary.append(indicators)
        self.overview_values = {}
        for key, title in [('charge', '当前电量'), ('source', '供电'), ('mode', '运行方式')]:
            metric = box(spacing=4)
            metric.append(label(title, True))
            value = label()
            value.add_css_class('dashboard-metric')
            metric.append(value)
            indicators.append(metric)
            self.overview_values[key] = value
        self.metrics = label('充电上限 -- · 电池功率 --', True)
        summary.append(self.metrics)
        modes = card(rows[1], '运行方式')
        self.mode_row = box(True)
        self.mode_row.set_homogeneous(True)
        modes.append(self.mode_row)
        self.mode_buttons = {}
        for key in MODES:
            self.add_mode(key)
        auto = card(rows[2], '跟随供电切换模式')
        self.auto = Gtk.CheckButton(label='开启自动切换', active=bool(self.settings.get('enabled')))
        auto.append(self.auto)
        self.auto.connect('toggled', lambda *_: self.save_policy())
        self.policy_choices = []
        self.mode_keys = []
        for text in ('插电', '离电'):
            row = box(True)
            caption = label(text, True)
            caption.set_width_chars(4)
            row.append(caption)
            choice = dropdown([])
            choice.set_hexpand(True)
            choice.set_sensitive(False)
            row.append(choice)
            auto.append(row)
            self.policy_choices.append(choice)
        startup = card(rows[0], '启动与托盘')
        autologin = Gtk.CheckButton(label='登录后自动启动到托盘', active=autostart.entry_path().is_file())
        startup.append(autologin)
        def set_autologin(widget):
            try:
                autostart.set_enabled(widget.get_active())
            except OSError as error:
                self.status.set_label(str(error))
        autologin.connect('toggled', set_autologin)
        admin = Gtk.CheckButton(label='从快捷方式打开时申请管理员权限',
                                active=self.settings.get('request_admin_at_launch') is True)
        startup.append(admin)
        admin.connect('toggled', lambda widget: self.persist(request_admin_at_launch=widget.get_active()))
        startup.append(label('收进托盘后继续运行。用托盘菜单的「退出」停止。', True))
        if backend_install.available():
            install_button = button('安装后端服务', lambda: confirm(
                self.window, '安装并启动 asusd？',
                '将使用界面包附带的 asusctl，替换同路径的源码安装文件。现有 asusd 配置会保留。',
                '申请授权并安装', lambda: self.install_backend(install_button)))
            install_button.set_halign(Gtk.Align.START)
            startup.append(install_button)
            self.refresh_backend_install(install_button)
        self.limit_controls = []
        self.limit_buffer = Gtk.EntryBuffer.new("--", 2)
        self.limit_row(card(rows[1], '照顾电池'))
        lights = card(rows[2], '键盘的光')
        row = box(True)
        lights.append(row)
        row.set_homogeneous(True)
        self.light_buttons = []
        for text, key in [('关闭', 'off'), ('柔和', 'low'), ('适中', 'med'), ('明亮', 'high')]:
            item = button(text, lambda key=key: self.submit(lambda: self.set_keyboard(key)))
            item.set_sensitive(False)
            row.append(item)
            self.light_buttons.append(item)

        self.aura_section = Gtk.Expander(label='Aura 键盘灯效', visible=False)
        lights.append(self.aura_section)
        self.build_keyboard_timeout(lights)
        self.build_bios(page)
        self.supply_section = Gtk.Expander(label='asusd 插拔电源规则', visible=False)
        auto.append(self.supply_section)

    def build_keyboard_timeout(self, parent):
        section = Gtk.Expander(label='闲置熄灯')
        parent.append(section)
        content = box()
        section.set_child(content)
        self.keyboard_settings = {}
        saved = self.settings.get('keyboard_timeout')
        saved = saved if isinstance(saved, dict) else {}
        labels = ['不自动关闭', *(f'{seconds} 秒' for seconds in LIGHT_TIMEOUTS[1:])]
        for source, title in (('ac', '插电'), ('battery', '离电')):
            row = box(True)
            caption = label(title, True)
            caption.set_width_chars(4)
            row.append(caption)
            choice = dropdown(labels)
            value = saved.get(source)
            choice.set_selected(LIGHT_TIMEOUTS.index(value) if value in LIGHT_TIMEOUTS else 0)
            choice.set_hexpand(True)
            row.append(choice)
            self.keyboard_settings[source] = choice
            content.append(row)
        for choice in self.keyboard_settings.values():
            choice.connect('notify::selected', self.save_keyboard_timeout)
        self.keyboard_note = label('需要 GNOME 会话的键盘灯和活动检测接口。', True)
        content.append(self.keyboard_note)

    def save_keyboard_timeout(self, *_):
        values = {source: LIGHT_TIMEOUTS[choice.get_selected()]
                  for source, choice in self.keyboard_settings.items()}
        self.persist(keyboard_timeout=values)

    def set_keyboard(self, level):
        result = self.backend.set_keyboard(level)
        if self.keyboard_idle:
            self.keyboard_idle.restore_level = None
        return result

    def tick_keyboard(self):
        if self.closing:
            return
        self.loop.after(1000, self.tick_keyboard)
        ac = self.state.get('ac')
        source = 'ac' if ac else 'battery'
        settings = self.settings.get('keyboard_timeout')
        seconds = settings.get(source, 0) if ac is not None and isinstance(settings, dict) else 0
        if seconds not in LIGHT_TIMEOUTS:
            seconds = 0
        restore_pending = self.keyboard_idle and self.keyboard_idle.restore_level is not None
        if self.keyboard_busy or (not seconds and not restore_pending):
            return
        self.keyboard_busy = True

        def update():
            try:
                if self.keyboard_idle is None:
                    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                    self.keyboard_idle = KeyboardIdle(connection, Gio, GLib)
                self.keyboard_idle.update(seconds)
            except GLib.Error:
                return '当前会话无法检测闲置时间或控制键盘灯。'
            return '恢复键盘或鼠标活动时会还原亮度。'

        self.submit(update, self.keyboard_note.set_label,
                    finished=lambda: setattr(self, 'keyboard_busy', False))

    def build_bios(self, page):
        section = card(page, 'BIOS')
        version, support = bios_info()
        section.append(label(f'当前版本 {version or "--"}'))
        actions = box(True)
        section.append(actions)
        self.bios_result = label('', True)
        section.append(self.bios_result)

        def check():
            check_button.set_sensitive(False)
            self.bios_result.set_label('正在检查系统 BIOS 更新…')

            def query():
                try:
                    return check_bios_updates()
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                    return f'检查失败：{error}'

            self.submit(query, self.bios_result.set_label,
                        finished=lambda: check_button.set_sensitive(True), executor=self.firmware_pool)

        check_button = button('检查更新', check)
        actions.append(check_button)
        if support:
            actions.append(Gtk.LinkButton(uri=support, label='ASUS 支持页面'))

    def refresh_backend_install(self, install_button):
        install_button.set_sensitive(False)

        def update(active):
            install_button.set_label('后端服务已运行' if active else '安装后端服务')
            install_button.set_sensitive(not active)

        self.submit(backend_install.service_active, update)

    def install_backend(self, install_button):
        install_button.set_sensitive(False)
        self.status.set_label('正在等待管理员授权或安装后端服务…')
        self.submit(backend_install.install, finished=lambda: self.refresh_backend_install(install_button))

    def populate_rog_controls(self, features):
        aura = features.get('aura')
        if aura:
            content = box()
            self.aura_section.set_child(content)
            self.aura_section.set_visible(True)
            color = Gtk.ColorButton()
            rgba = Gdk.RGBA()
            rgba.parse('#' + ''.join(f'{channel:02x}' for channel in aura['color']))
            color.set_rgba(rgba)
            color.set_title('键盘颜色')
            row = box(True)
            row.append(label('键盘颜色'))
            row.append(color)
            content.append(row)
            checks = []
            power_row = box(True, 8)
            content.append(power_row)
            for name, enabled in zip(POWER_FIELDS, aura['power']):
                widget = Gtk.CheckButton(label=name, active=enabled)
                checks.append(widget)
                power_row.append(widget)
            def save_aura():
                current = color.get_rgba()
                rgb = [round(channel * 255) for channel in (current.red, current.green, current.blue)]
                states = [widget.get_active() for widget in checks]
                submit.set_sensitive(False)
                self.submit(lambda: self.backend.save_aura(aura['path'], rgb, states),
                            finished=lambda: submit.set_sensitive(True))
            submit = button('保存灯效', save_aura)
            submit.set_halign(Gtk.Align.START)
            content.append(submit)

        supply = features.get('supply')
        if supply and self.mode_keys:
            content = box()
            self.supply_section.set_child(content)
            self.supply_section.set_visible(True)
            content.append(label('由 asusd 在后台执行。启用任一规则后，本应用的自动切换会关闭。', True))
            rule_widgets = {}
            for source, title in [('ac', '插电'), ('battery', '离电')]:
                enabled, current = supply[source]
                row = box(True)
                toggle = Gtk.CheckButton(label=title, active=enabled)
                selector = dropdown([MODENAMES.get(mode, mode) for mode in self.mode_keys])
                selector.set_selected(self.mode_keys.index(current) if current in self.mode_keys
                                      else Gtk.INVALID_LIST_POSITION)
                row.append(toggle)
                row.append(selector)
                content.append(row)
                rule_widgets[source] = (toggle, selector)
            def save_supply():
                rules = {}
                for source, (toggle, selector) in rule_widgets.items():
                    index = selector.get_selected()
                    if index >= len(self.mode_keys):
                        self.status.set_label('请选择插电和离电模式')
                        return
                    rules[source] = (toggle.get_active(), self.mode_keys[index])
                save_button.set_sensitive(False)
                def done(message):
                    if any(enabled for enabled, _ in rules.values()):
                        self.auto.set_active(False)
                    self.status.set_label(message)
                self.submit(lambda: self.backend.save_supply_rules(rules), done,
                            finished=lambda: save_button.set_sensitive(True))
            save_button = button('保存 asusd 规则', save_supply)
            save_button.set_halign(Gtk.Align.START)
            content.append(save_button)

    def add_mode(self, key):
        widget = button(MODENAMES.get(key, key),
                        lambda: self.submit(lambda: self.backend.set_profile(key)))
        widget.set_sensitive(False)
        self.mode_row.append(widget)
        self.mode_buttons[key] = widget

    def persist(self, **values):
        proposed = dict(self.settings, **values)
        try:
            save(proposed)
        except OSError as error:
            self.status.set_label(f'保存失败：{error}')
            return False
        self.settings = proposed
        return True

    def save_policy(self):
        values = {'enabled': self.auto.get_active()}
        for key, choice in zip(('ac', 'battery'), self.policy_choices):
            index = choice.get_selected()
            if index < len(self.mode_keys):
                values[key] = self.mode_keys[index]
        self.persist(**values)

    def limit_row(self, parent):
        row = box(True)
        parent.append(row)
        entry = Gtk.Entry(buffer=self.limit_buffer, width_chars=5)
        row.append(entry)
        row.append(label('%'))
        def apply():
            try:
                value = int(entry.get_text())
            except ValueError:
                self.status.set_label('请输入 20–100 的整数')
                return
            self.submit(lambda: self.backend.set_limit(value))
        save_button = button('保存上限', apply)
        row.append(save_button)
        entry.set_sensitive(False)
        save_button.set_sensitive(False)
        self.limit_controls.append((entry, save_button))

    def build_battery(self):
        page = self.pages['battery']
        summary = card(page, '电池状态')
        self.battery_summary = label('--', True)
        summary.append(self.battery_summary)
        metrics = box(True, 12)
        metrics.set_homogeneous(True)
        summary.append(metrics)
        self.battery_metrics = {}
        for key, title in [('percent', '当前电量'), ('health', '健康度'),
                           ('power', '电池功率'), ('time', '预计时间')]:
            metric = box(spacing=4)
            caption, value = label(title, True), label()
            value.add_css_class('dashboard-metric')
            metric.append(caption)
            metric.append(value)
            metrics.append(metric)
            self.battery_metrics[key] = (caption, value)
        self.battery_note = label('', True)
        summary.append(self.battery_note)
        self.battery_values = {}
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
                self.battery_values[key] = value
        charging = card(page, '充电上限')
        self.limit_row(charging)
        self.full_charge_button = button('一次充满', self.one_shot_charge)
        self.full_charge_button.set_sensitive(False)
        self.full_charge_button.set_halign(Gtk.Align.START)
        charging.append(self.full_charge_button)

    def one_shot_charge(self):
        def apply():
            self.full_charge_button.set_sensitive(False)
            self.submit(self.backend.one_shot_charge,
                        finished=lambda: self.full_charge_button.set_sensitive(True))
        confirm(self.window, '这次充电充至 100%？',
                'asusd 会临时提高充电上限，并负责恢复原上限。', '一次充满', apply)

    def build_cooling(self):
        page = self.pages['cooling']
        readings = card(page, '实时温度与转速')
        columns = box(True, 24)
        columns.set_homogeneous(True)
        readings.append(columns)
        self.sensor_columns = {}
        self.sensor_rows = {'temperature': {}, 'fan': {}}
        for key, heading in [('temperature', '温度'), ('fan', '风扇转速')]:
            column = box(spacing=6)
            columns.append(column)
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
        self.gpu_apply = button('应用', self.apply_gpu)
        self.gpu_reset = button('恢复自动调频', lambda: self.gpu_operation(self.backend.set_igpu_max))
        self.gpu_install = button('安装控制组件', lambda: self.gpu_operation(self.backend.install_igpu_helper))
        for widget in (self.gpu_apply, self.gpu_reset, self.gpu_install):
            row.append(widget)
            widget.set_sensitive(False)
        self.gpu_target.set_sensitive(False)
        self.gpu_busy = False

    def refresh_sensors(self, sensors):
        groups = {'temperature': {}, 'fan': {}}
        for name, value in sensors.items():
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

    def show_fans(self):
        self.stack.set_visible_child_name('combination')
        self.combination.sections.set_visible_child_name('fans')

    def apply_gpu(self):
        try:
            value = int(self.gpu_target.get_text())
        except ValueError:
            self.status.set_label('请输入整数 MHz')
            return
        self.gpu_operation(lambda: self.backend.set_igpu_max(value))

    def gpu_operation(self, command):
        if self.gpu_busy:
            return
        self.gpu_busy = True
        self.refresh_gpu(self.state.get('igpu_clock', self.caps.get('igpu_clock', {})))
        def complete():
            self.gpu_busy = False
            self.submit(self.backend.igpu_clock, lambda info: self.refresh_gpu(info, update_input=True))
        self.submit(command, finished=complete)

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

    def ready(self, result):
        self.backend, self.caps = result
        caps = self.caps
        self.mode_keys = caps['profiles']
        for key in self.mode_keys:
            if key not in self.mode_buttons:
                self.add_mode(key)
        for key, widget in self.mode_buttons.items():
            widget.set_visible(key in self.mode_keys)
            widget.set_sensitive(caps['modern_cli'])
        for choice, key, default in zip(self.policy_choices, ('ac', 'battery'), ('balanced', 'quiet')):
            choice.set_model(Gtk.StringList.new([MODENAMES.get(mode, mode) for mode in self.mode_keys]))
            desired = self.settings.get(key, default)
            choice.set_selected(self.mode_keys.index(desired) if desired in self.mode_keys else Gtk.INVALID_LIST_POSITION)
            choice.set_sensitive(bool(self.mode_keys))
            choice.connect('notify::selected', lambda *_: self.save_policy())
        for widget in self.light_buttons:
            widget.set_sensitive(caps['keyboard'] and caps['modern_cli'])
        for entry, action in self.limit_controls:
            enabled = caps['charge_limit'] and caps['modern_cli']
            entry.set_sensitive(enabled)
            action.set_sensitive(enabled)
        self.full_charge_button.set_sensitive(caps['charge_limit'])
        self.populate_rog_controls(caps.get('rog_features', {}))
        self.combination.populate(caps)
        self.refresh_gpu(caps.get('igpu_clock', {}))
        self.status.set_label('准备好了')
        self.tick()

    def tick(self):
        if not self.closing:
            self.submit(self.backend.snapshot, self.update,
                        finished=lambda: self.loop.after(3000, self.tick))

    def update(self, state):
        self.state = state
        ac = state['ac']
        source = '外接电源' if ac is True else '电池供电' if ac is False else '供电 --'
        self.overview_values['charge'].set_label(fmt(state['battery'], '%'))
        self.overview_values['source'].set_label(source if ac is not None else '--')
        self.overview_values['mode'].set_label(MODENAMES.get(state['profile'], state['profile']) or '--')
        self.metrics.set_label(f"充电上限 {fmt(state['limit'], '%')} · 电池功率 {fmt(state['battery_watts'], ' W')}")
        for entry, _ in self.limit_controls:
            if entry.get_text() == '--' and state['limit'] is not None:
                entry.set_text(str(int(state['limit'])))
        battery = formatted_values(state.get('battery_info') or {})
        self.battery_summary.set_label(battery['summary'])
        self.battery_note.set_label(battery['note'])
        for key, (caption, value) in self.battery_metrics.items():
            if key in ('power', 'time'):
                caption.set_label(battery[f'{key}_title'])
            value.set_label(battery[key])
        for key, widget in self.battery_values.items():
            widget.set_label(battery.get(key, '--'))
        self.refresh_sensors(state['sensors'])
        self.refresh_gpu(state.get('igpu_clock', {}))
        self.combination.refresh(state)
        mode = self.policy.target(ac, self.settings)
        profile = state['profile']
        if mode and mode != profile and mode in self.mode_keys and self.backend.modern:
            self.submit(lambda: self.backend.switch_profile_on_power_change(mode, profile, ac))
            return
        request = self.combination_policy.request(state, self.combinations)
        if request and (request[2]['power'] or request[2]['fans']):
            self.submit(lambda: self.backend.apply_combination(*request))


class CombinationEditor:
    def __init__(self, controller, page):
        self.controller = controller
        self.draft = deepcopy(controller.combinations)
        self.selected = ('balanced', 'ac')
        self.loading = False
        self.busy = False
        self.power_rows = {}
        self.epp_choices = []
        self.templates = {}
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
        self.enabled = Gtk.CheckButton(label='自动联动（保存后生效）', active=self.draft['enabled'])
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
            self.templates[fan_info['initial_profile']] = fan_info['curves']
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
        self.power_rows[key] = (checked, field, reading, default, data)

        self.load_power_row(key)

    def load_power_row(self, key):
        check, field, _, _, data = self.power_rows[key]
        power = self.current_entry()['power']
        check.set_active(key in power)
        value = power.get(key, data['current'])
        # Saved values may belong to a different supply context.
        field.set_range(min(value, data['minimum']), max(value, data['maximum']))
        field.set_increments(data['step'], data['step'])
        field.set_value(value)

    def fill_default(self, key):
        check, field, _, _, data = self.power_rows[key]
        if data.get('default') is not None:
            field.set_value(data['default'])
            check.set_active(True)

    def current_entry(self):
        mode, source = self.selected
        return self.draft['entries'][mode][source]

    def collect(self):
        entry = deepcopy(self.current_entry())
        for key, (check, field, _, _, _) in self.power_rows.items():
            if check.get_active():
                field.update()
                entry['power'][key] = field.get_value_as_int()
            else:
                entry['power'].pop(key, None)
        entry['fans'] = self.fan.export()
        mode, source = self.selected
        self.draft['entries'][mode][source] = entry
        self.draft['enabled'] = self.enabled.get_active()

    def load_entry(self):
        entry = self.current_entry()
        for key in self.power_rows:
            self.load_power_row(key)
        self.fan.load(entry['fans'] or self.templates.get(self.selected[0], {}), bool(entry['fans']))

    def switch(self):
        if self.loading:
            return
        try:
            self.collect()
        except ValueError as error:
            self.note.set_label(str(error))
            self.loading = True
            self.mode.set_selected(MODES.index(self.selected[0]))
            self.source.set_selected(int(self.selected[1] == 'battery'))
            self.loading = False
            return
        self.selected = MODES[self.mode.get_selected()], ('ac', 'battery')[self.source.get_selected()]
        self.load_entry()

    def save(self):
        try:
            self.collect()
            settings = normalized(self.draft)
        except ValueError as error:
            self.note.set_label(str(error))
            return
        if self.controller.persist(combinations=settings):
            self.controller.combinations = settings
            self.controller.combination_policy.previous = None
            self.note.set_label('六套组合已保存')

    def apply(self):
        if not self.controller.backend or self.busy:
            return
        try:
            self.collect()
            entry = effective(self.draft, *self.selected)
        except ValueError as error:
            self.note.set_label(str(error))
            return
        mode, source = self.selected
        self.busy = True
        self.note.set_label('正在应用组合…')
        def done(message):
            self.note.set_label(message)
        def finished():
            self.busy = False
            if self.note.get_label() == '正在应用组合…':
                self.note.set_label(self.controller.status.get_label())
        self.controller.submit(lambda: self.controller.backend.apply_combination(mode, source, entry),
                               done, finished)

    def save_epp(self):
        profiles = {mode: EPP[choice.get_selected()] for mode, choice in zip(MODES, self.epp_choices)}
        linked = self.epp_linked.get_active()
        self.epp_save.set_sensitive(False)
        self.controller.submit(lambda: self.controller.backend.save_profile_epp(linked, profiles),
                               finished=lambda: self.epp_save.set_sensitive(True))

    def read_curves(self):
        if not self.controller.backend:
            return
        if self.fan.managed.get_active():
            self.note.set_label('取消勾选「管理风扇曲线」后可重新读取方案曲线。')
            return
        selected = self.selected
        def done(curves):
            self.templates[selected[0]] = deepcopy(curves)
            if self.selected == selected and not self.fan.managed.get_active():
                self.fan.load(curves, False)
        self.controller.submit(lambda: self.controller.backend.read_fan_curves(selected[0]), done)

    def restore_curves(self):
        selected = self.selected
        mode, source = selected
        def apply():
            self.reset_fans.set_sensitive(False)
            def done(curves):
                self.templates[mode] = deepcopy(curves)
                self.draft['entries'][mode][source]['fans'] = deepcopy(curves)
                if self.selected == selected:
                    self.fan.load(curves, True)
                self.note.set_label('asusd 曲线已恢复。保存全部配置可更新当前组合。')
            self.controller.submit(lambda: self.controller.backend.reset_fan_curves(mode), done,
                                   finished=lambda: self.reset_fans.set_sensitive(True))
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
        mode, source = self.selected
        target = 'battery' if source == 'ac' else 'ac'
        entry = self.draft['entries'][mode][target]
        def copy():
            entry['fans'] = deepcopy(curves)
            self.note.set_label('已复制到另一种供电的草稿')
        if entry['fans']:
            dialog = Adw.MessageDialog(transient_for=self.controller.window, modal=True,
                                       heading='覆盖另一种供电的曲线？')
            dialog.add_response('cancel', '取消')
            dialog.add_response('copy', '覆盖')
            dialog.set_close_response('cancel')
            dialog.connect('response::copy', lambda *_: copy())
            dialog.present()
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
                self.power_rows[key] = (check, field, reading, default, actual)
            unit = attribute_unit(key)
            text = f"实际 {actual['current']} {unit} · 范围 {actual['minimum']}–{actual['maximum']} {unit}"
            if actual.get('pending') is not None:
                text += f" · 待应用 {actual['pending']} {unit}"
            reading.set_label(text)
            default.set_sensitive(actual.get('default') is not None)


class CurveEditor:
    def __init__(self, parent):
        self.curves = {}
        self.keys = []
        self.active = None
        self.selected = 0
        self.loading = False
        self.drag = None
        row = box(True)
        parent.append(row)
        self.managed = Gtk.CheckButton(label='管理风扇曲线')
        self.switcher = dropdown([])
        self.enabled = Gtk.CheckButton(label='启用此曲线')
        for widget in (self.managed, self.switcher, self.enabled):
            row.append(widget)
        self.note = label('--', True)
        parent.append(self.note)
        self.plot = Gtk.DrawingArea(content_height=205)
        parent.append(self.plot)
        self.plot.set_draw_func(self.draw)
        gesture = Gtk.GestureDrag()
        gesture.connect('drag-begin', self.drag_begin)
        gesture.connect('drag-update', self.drag_update)
        gesture.connect('drag-end', lambda *_: setattr(self, 'drag', None))
        self.plot.add_controller(gesture)
        table = box(True, 20)
        table.set_homogeneous(True)
        table.set_margin_start(36)
        table.set_margin_end(36)
        parent.append(table)
        self.inputs = []
        for block in range(2):
            grid = Gtk.Grid(column_spacing=16, row_spacing=6,
                            halign=Gtk.Align.START if block == 0 else Gtk.Align.END)
            table.append(grid)
            for column, text in enumerate(('点', '温度 °C', '风扇输出 %')):
                grid.attach(label(text, True), column, 0, 1, 1)
            for row_index in range(4):
                index = block * 4 + row_index
                grid.attach(button(str(index + 1), lambda index=index: self.select(index)), 0, row_index + 1, 1, 1)
                fields = []
                for column in (1, 2):
                    field = Gtk.SpinButton.new_with_range(0, 100, 1 if column == 1 else .1)
                    field.set_digits(0 if column == 1 else 1)
                    field.set_width_chars(5)
                    field.connect('value-changed', lambda *_, index=index: self.select(index))
                    grid.attach(field, column, row_index + 1, 1, 1)
                    fields.append(field)
                self.inputs.append(fields)
        self.managed.connect('toggled', lambda *_: self.sensitivity())
        self.switcher.connect('notify::selected', lambda *_: self.switch())
        self.load({}, False)

    def sensitivity(self):
        enabled = bool(self.active and self.managed.get_active())
        self.enabled.set_sensitive(enabled)
        for pair in self.inputs:
            for field in pair:
                field.set_sensitive(enabled)

    def load(self, curves, managed):
        self.loading = True
        self.curves = deepcopy(curves)
        old = self.active
        self.keys = list(curves)
        self.switcher.set_model(Gtk.StringList.new([FAN_NAMES[key] for key in self.keys]))
        key = old if old in curves else next(iter(curves), None)
        self.switcher.set_selected(self.keys.index(key) if key else Gtk.INVALID_LIST_POSITION)
        self.managed.set_active(managed)
        self.show_curve(key)
        self.loading = False

    def show_curve(self, key):
        self.active = key
        curve = self.curves.get(key)
        self.enabled.set_active(bool(curve and curve['enabled']))
        for index, (temp, pwm) in enumerate(self.inputs):
            temp.set_value(curve['temp'][index] if curve else 0)
            pwm.set_value(round(curve['pwm'][index] * 100 / 255, 1) if curve else 0)
        self.note.set_label('重合点共用编号标记。可拖动曲线，也可编辑下方数值。' if curve else '请先读取方案曲线。')
        self.sensitivity()
        self.plot.queue_draw()

    def read_points(self):
        original = self.curves[self.active]
        curve = {'fan': self.active, 'temp': [], 'pwm': [], 'enabled': self.enabled.get_active()}
        for index, (temp, pwm) in enumerate(self.inputs):
            temp.update()
            pwm.update()
            curve['temp'].append(temp.get_value_as_int())
            old = original['pwm'][index]
            percent = pwm.get_value()
            curve['pwm'].append(old if abs(percent - round(old * 100 / 255, 1)) < .0001 else round(percent * 255 / 100))
        validate_curve(curve)
        return curve

    def switch(self):
        index = self.switcher.get_selected()
        if self.loading or index >= len(self.keys):
            return
        self.loading = True
        try:
            if self.active and self.managed.get_active():
                self.curves[self.active] = self.read_points()
            self.show_curve(self.keys[index])
        except ValueError as error:
            self.switcher.set_selected(self.keys.index(self.active))
            self.note.set_label(str(error))
        finally:
            self.loading = False

    def export(self):
        if not self.managed.get_active():
            return {}
        if self.active is None:
            raise ValueError('请先读取方案曲线')
        self.curves[self.active] = self.read_points()
        return deepcopy(self.curves)

    def select(self, index):
        if not self.loading:
            self.selected = index
            self.plot.queue_draw()

    def points(self, width, height):
        return [(38 + temp.get_value() * (width - 62) / 100,
                 height - 28 - pwm.get_value() * (height - 48) / 100) for temp, pwm in self.inputs]

    def draw(self, widget, context, width, height):
        style = widget.get_style_context()
        fg = style.get_color()
        found, accent = style.lookup_color('accent_color')
        if not found:
            accent = fg
        context.set_source_rgba(fg.red, fg.green, fg.blue, .24)
        for value in (0, 25, 50, 75, 100):
            y = height - 28 - value * (height - 48) / 100
            context.move_to(38, y)
            context.line_to(width - 24, y)
            context.stroke()
            context.move_to(7, y + 4)
            context.show_text(str(value))
            context.move_to(31 + value * (width - 62) / 100, height - 7)
            context.show_text(str(value))
        if not self.active:
            return
        points = self.points(width, height)
        context.set_source_rgb(accent.red, accent.green, accent.blue)
        context.set_line_width(2)
        context.move_to(*points[0])
        for point in points[1:]:
            context.line_to(*point)
        context.stroke()
        groups = {}
        for index, point in enumerate(points):
            groups.setdefault(point, []).append(index)
        for (x, y), indices in groups.items():
            context.arc(x, y, 6 if self.selected in indices else 4, 0, 6.2832)
            context.fill()
            context.move_to(x - 22 if x > width - 50 else x + 8, y - 5 if y > 24 else y + 15)
            context.show_text('/'.join(str(index + 1) for index in indices))

    def drag_begin(self, gesture, x, y):
        if not self.active or not self.managed.get_active():
            return
        points = self.points(self.plot.get_width(), self.plot.get_height())
        distances = [(px - x) ** 2 + (py - y) ** 2 for px, py in points]
        distance = min(distances)
        if distance > 225:
            return
        choices = [i for i, value in enumerate(distances) if value == distance]
        index = choices[(choices.index(self.selected) + 1) % len(choices)] if self.selected in choices else choices[0]
        self.select(index)
        self.drag = index, *points[index]

    def drag_update(self, gesture, dx, dy):
        if self.drag is None:
            return
        index, x, y = self.drag
        width, height = self.plot.get_width(), self.plot.get_height()
        for column, value in enumerate(((x + dx - 38) * 100 / (width - 62),
                                        (height - 28 - y - dy) * 100 / (height - 48))):
            lower = self.inputs[index - 1][column].get_value() if index > 0 else 0
            upper = self.inputs[index + 1][column].get_value() if index < 7 else 100
            self.inputs[index][column].set_value(round(max(lower, min(upper, value)), column))


def launch(start_hidden=False):
    GLib.set_prgname('asus-power-controller')
    GLib.set_application_name('ASUS Power Controller')
    instance = Instance(start_hidden)
    if not instance.acquire():
        return
    app = Adw.Application(flags=Gio.ApplicationFlags.NON_UNIQUE)
    controller = None
    def activate(application):
        nonlocal controller
        if controller is None:
            controller = Controller(application, instance, start_hidden)
        else:
            controller.window.present()
    app.connect('activate', activate)
    try:
        app.run([])
    finally:
        if controller:
            controller.pool.shutdown(wait=True, cancel_futures=True)
            if controller.keyboard_idle:
                try:
                    controller.keyboard_idle.restore()
                except GLib.Error:
                    pass
            controller.firmware_pool.shutdown(wait=False, cancel_futures=True)
        instance.close()
