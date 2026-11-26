"""Daily controls: operating modes, startup, keyboard and firmware."""
import subprocess

from .. import autostart, backend_install
from ..combinations import MODES
from ..hardware.firmware import bios_info, check_bios_updates
from ..hardware.rog_features import POWER_FIELDS
from .gtk import Gdk, Gtk
from .widgets import MODENAMES, box, button, card, confirm, dropdown, fmt, label

LIGHT_TIMEOUTS = (0, 15, 30, 60, 120, 300)


class OverviewPage:
    def __init__(self, controller, page, charge):
        self.controller = controller
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
        self.values = {}
        for key, title in [('charge', '当前电量'), ('source', '供电'), ('mode', '运行方式')]:
            metric = box(spacing=4)
            metric.append(label(title, True))
            value = label()
            value.add_css_class('dashboard-metric')
            metric.append(value)
            indicators.append(metric)
            self.values[key] = value
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
        self.auto = Gtk.CheckButton(label='开启自动切换', active=bool(controller.settings.get('enabled')))
        auto.append(self.auto)
        self.auto.connect('toggled', lambda *_: self.save_policy())
        self.policy_choices = []
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
        charge.append(card(rows[1], '照顾电池'))
        lights = card(rows[2], '键盘的光')
        row = box(True)
        lights.append(row)
        row.set_homogeneous(True)
        self.light_buttons = []
        for text, key in [('关闭', 'off'), ('柔和', 'low'), ('适中', 'med'), ('明亮', 'high')]:
            item = button(text, lambda key=key: controller.submit(lambda: self.set_keyboard(key)))
            item.set_sensitive(False)
            row.append(item)
            self.light_buttons.append(item)
        self.aura_section = Gtk.Expander(label='Aura 键盘灯效', visible=False)
        lights.append(self.aura_section)
        self.build_keyboard_timeout(lights)
        self.build_bios(page)
        self.supply_section = Gtk.Expander(label='asusd 插拔电源规则', visible=False)
        auto.append(self.supply_section)

    def add_mode(self, key):
        widget = button(MODENAMES.get(key, key),
                        lambda: self.controller.submit(lambda: self.controller.backend.set_profile(key)))
        widget.set_sensitive(False)
        self.mode_row.append(widget)
        self.mode_buttons[key] = widget

    def ready(self, caps):
        modes = self.controller.mode_keys
        for key in modes:
            if key not in self.mode_buttons:
                self.add_mode(key)
        for key, widget in self.mode_buttons.items():
            widget.set_visible(key in modes)
            widget.set_sensitive(caps['modern_cli'])
        for choice, key, default in zip(self.policy_choices, ('ac', 'battery'), ('balanced', 'quiet')):
            choice.set_model(Gtk.StringList.new([MODENAMES.get(mode, mode) for mode in modes]))
            desired = self.controller.settings.get(key, default)
            choice.set_selected(modes.index(desired) if desired in modes else Gtk.INVALID_LIST_POSITION)
            choice.set_sensitive(bool(modes))
            choice.connect('notify::selected', lambda *_: self.save_policy())
        for widget in self.light_buttons:
            widget.set_sensitive(caps['keyboard'] and caps['modern_cli'])
        self.populate_rog_controls(caps.get('rog_features', {}))

    def refresh(self, state):
        ac = state['ac']
        self.values['charge'].set_label(fmt(state['battery'], '%'))
        self.values['source'].set_label('外接电源' if ac is True else '电池供电' if ac is False else '--')
        self.values['mode'].set_label(MODENAMES.get(state['profile'], state['profile']) or '--')
        self.metrics.set_label(f"充电上限 {fmt(state['limit'], '%')} · 电池功率 {fmt(state['battery_watts'], ' W')}")

    def save_policy(self):
        values = {'enabled': self.auto.get_active()}
        for key, choice in zip(('ac', 'battery'), self.policy_choices):
            index = choice.get_selected()
            if index < len(self.controller.mode_keys):
                values[key] = self.controller.mode_keys[index]
        self.controller.persist(**values)

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
        actions = box(True)
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
        actions.append(check_button)
        if support:
            actions.append(Gtk.LinkButton(uri=support, label='ASUS 支持页面'))

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
                c.perform(lambda: c.backend.save_aura(aura['path'], rgb, states), control=submit)

            submit = button('保存灯效', save_aura)
            submit.set_halign(Gtk.Align.START)
            content.append(submit)

        supply = features.get('supply')
        if supply and c.mode_keys:
            content = box()
            self.supply_section.set_child(content)
            self.supply_section.set_visible(True)
            content.append(label('由 asusd 在后台执行。启用任一规则后，本应用的自动切换会关闭。', True))
            rule_widgets = {}
            for source, title in [('ac', '插电'), ('battery', '离电')]:
                enabled, current = supply[source]
                row = box(True)
                toggle = Gtk.CheckButton(label=title, active=enabled)
                selector = dropdown([MODENAMES.get(mode, mode) for mode in c.mode_keys])
                selector.set_selected(c.mode_keys.index(current) if current in c.mode_keys
                                      else Gtk.INVALID_LIST_POSITION)
                row.append(toggle)
                row.append(selector)
                content.append(row)
                rule_widgets[source] = toggle, selector

            def save_supply():
                rules = {}
                for source, (toggle, selector) in rule_widgets.items():
                    index = selector.get_selected()
                    if index >= len(c.mode_keys):
                        c.status.set_label('请选择插电和离电模式')
                        return
                    rules[source] = toggle.get_active(), c.mode_keys[index]

                def done(message):
                    if any(enabled for enabled, _ in rules.values()):
                        self.auto.set_active(False)
                    c.status.set_label(message)

                c.perform(lambda: c.backend.save_supply_rules(rules), control=save_button, done=done)

            save_button = button('保存 asusd 规则', save_supply)
            save_button.set_halign(Gtk.Align.START)
            content.append(save_button)
