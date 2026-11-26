"""Application startup, keyboard lighting and firmware settings."""
import subprocess

from .. import autostart, backend_install
from ..hardware.firmware import bios_info, check_bios_updates
from ..hardware.rog_features import POWER_FIELDS
from .gtk import Gdk, Gtk
from .widgets import box, button, card, confirm, dropdown, flow, label

LIGHT_TIMEOUTS = (0, 15, 30, 60, 120, 300)


class SettingsPage:
    def __init__(self, controller, page):
        self.controller = controller
        startup = card(page, '启动与托盘')
        autologin = Gtk.CheckButton(label='登录后自动启动到托盘', active=autostart.entry_path().is_file())
        startup.append(autologin)

        def set_autologin(widget):
            try:
                autostart.set_enabled(widget.get_active())
            except OSError as error:
                controller.status.set_label(str(error))

        autologin.connect('toggled', set_autologin)
        admin = Gtk.CheckButton(label='从快捷方式打开时申请管理员权限',
                                active=controller.settings.get('request_admin_at_launch') is True)
        startup.append(admin)
        admin.connect('toggled', lambda widget: controller.persist(request_admin_at_launch=widget.get_active()))
        startup.append(label('收进托盘后继续运行。用托盘菜单的「退出」停止。', True))
        if backend_install.available():
            install_button = button('安装后端服务', lambda: confirm(
                controller.window, '安装并启动 asusd？',
                '将使用界面包附带的 asusctl，替换同路径的源码安装文件。现有 asusd 配置会保留。',
                '申请授权并安装', lambda: self.install_backend(install_button)))
            install_button.set_halign(Gtk.Align.START)
            startup.append(install_button)
            self.refresh_backend_install(install_button)
        lights = card(page, '键盘灯光')
        row = flow(maximum=4, homogeneous=True)
        lights.append(row)
        self.light_buttons = []
        for text, key in [('关闭', 'off'), ('柔和', 'low'), ('适中', 'med'), ('明亮', 'high')]:
            item = button(text, lambda key=key: controller.submit(lambda: self.set_keyboard(key)))
            item.set_sensitive(False)
            row.insert(item, -1)
            self.light_buttons.append(item)
        self.aura_section = Gtk.Expander(label='Aura 键盘灯效', visible=False)
        lights.append(self.aura_section)
        self.build_keyboard_timeout(lights)
        self.build_bios(page)

    def ready(self, caps):
        for widget in self.light_buttons:
            widget.set_sensitive(caps['keyboard'] and caps['modern_cli'])
        self.populate_rog_controls(caps.get('rog_features', {}))

    def build_keyboard_timeout(self, parent):
        section = Gtk.Expander(label='闲置熄灯')
        parent.append(section)
        content = box()
        section.set_child(content)
        self.keyboard_settings = {}
        saved = self.controller.settings.get('keyboard_timeout')
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
        self.controller.persist(keyboard_timeout=values)

    def set_keyboard(self, level):
        result = self.controller.backend.set_keyboard(level)
        if self.controller.keyboard_idle:
            self.controller.keyboard_idle.restore_level = None
        return result

    def build_bios(self, page):
        section = card(page, 'BIOS')
        version, support = bios_info()
        section.append(label(f'当前版本 {version or "--"}'))
        actions = flow(maximum=2)
        section.append(actions)
        self.bios_result = label('', True)
        section.append(self.bios_result)

        def check():
            def query():
                try:
                    return check_bios_updates()
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                    return f'检查失败：{error}'

            self.controller.perform(query, control=check_button, notice=self.bios_result,
                                    pending='正在检查系统 BIOS 更新…',
                                    executor=self.controller.firmware_pool)

        check_button = button('检查更新', check)
        actions.insert(check_button, -1)
        if support:
            actions.insert(Gtk.LinkButton(uri=support, label='ASUS 支持页面'), -1)

    def refresh_backend_install(self, install_button):
        install_button.set_sensitive(False)

        def update(active):
            install_button.set_label('后端服务已运行' if active else '安装后端服务')
            install_button.set_sensitive(not active)

        self.controller.submit(backend_install.service_active, update)

    def install_backend(self, install_button):
        install_button.set_sensitive(False)
        self.controller.status.set_label('正在等待管理员授权或安装后端服务…')
        self.controller.submit(backend_install.install,
                               finished=lambda: self.refresh_backend_install(install_button))

    def populate_rog_controls(self, features):
        c = self.controller
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
            power_row = flow(maximum=4)
            content.append(power_row)
            for name, enabled in zip(POWER_FIELDS, aura['power']):
                widget = Gtk.CheckButton(label=name, active=enabled)
                checks.append(widget)
                power_row.insert(widget, -1)

            def save_aura():
                current = color.get_rgba()
                rgb = [round(channel * 255) for channel in (current.red, current.green, current.blue)]
                states = [widget.get_active() for widget in checks]
                c.perform(lambda: c.backend.save_aura(aura['path'], rgb, states), control=submit)

            submit = button('保存灯效', save_aura)
            submit.set_halign(Gtk.Align.START)
            content.append(submit)
