import tkinter as tk
from tkinter import ttk, messagebox
from concurrent.futures import ThreadPoolExecutor
from .backend import LinuxBackend
from . import autostart
from .battery_panel import BatteryPanel
from .combination_panel import CombinationPanel
from .combinations import normalized, CombinationPolicy
from .config import load, save, Policy
from .display import Display
from .scroll_page import ScrollViewport
from .theme import RoundedTheme, system_theme

EPP_NAMES = {'default': '固件默认', 'performance': '性能优先', 'balance_performance': '均衡偏性能',
             'balance_power': '均衡偏省电', 'power': '省电优先'}

NAMES = {'quiet': '静音', 'balanced': '均衡', 'performance': '性能', 'low-power': '节能', 'custom': '自定义'}


def configure_changed(widget, **options):
    """Avoid invalidating Tk layout for identical size/text notifications."""
    previous = getattr(widget, '_applied_options', {})
    changed = {key: value for key, value in options.items() if previous.get(key) != value}
    if changed:
        widget.configure(**changed)
        previous.update(changed)
        widget._applied_options = previous


class Application:
    def __init__(self, root):
        self.root = root
        self.show_after_theme = root.state() == 'withdrawn'
        self.display = Display(root)
        self.px = self.display.px
        self.wrapping = []
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.policy = Policy()
        self.settings = load()
        self.combination_policy = CombinationPolicy()
        self.combination_error = None
        try:
            self.combination_settings = normalized(self.settings.get('combinations'))
        except ValueError as error:
            self.combination_settings = normalized(None)
            self.combination_error = str(error)
        self.backend = None
        self.closed = False
        root.title('ASUS Power Controller')
        width = min(self.px(900), root.winfo_screenwidth() - self.px(40))
        height = min(self.px(760), root.winfo_screenheight() - self.px(80))
        root.geometry(f'{width}x{height}')
        root.minsize(min(self.px(720), width), min(self.px(520), height))
        self.theme = RoundedTheme(root, self.display)
        self.theme.bind_colors(root, bg='bg')
        self.theme_pool = ThreadPoolExecutor(max_workers=1)
        header = tk.Frame(root)
        self.theme.bind_colors(header, bg='bg')
        header.pack(fill='x', padx=self.px(30), pady=self.px((24, 20)))
        brand = tk.Label(header, text='ASUS Power Controller', font=self.display.font(10))
        self.theme.bind_colors(brand, bg='bg', fg='muted')
        brand.pack(anchor='w')
        title = tk.Label(header, text='电脑管家', font=self.display.font(23))
        self.theme.bind_colors(title, bg='bg', fg='text')
        title.pack(anchor='w', pady=self.px((4, 5)))
        self.model = tk.Label(header, text='--')
        self.theme.bind_colors(self.model, bg='bg', fg='muted')
        self.model.pack(anchor='w', pady=self.px((4, 0)))
        self.tabs = ttk.Notebook(root)
        self.tabs.pack(fill='both', expand=True, padx=self.px(24))
        self.overview = self.page('日常设置')
        self.advanced = self.page('散热调节')
        self.advanced.configure(style='Rounded.TFrame', padding=self.px((20, 12, 20, 16)))
        self.battery_page = self.page('电池信息')
        self.combination_page = self.tab(self.tabs, '组合策略')
        self.status = tk.StringVar(value='正在了解你的电脑…')
        status_label = tk.Label(root, textvariable=self.status, anchor='w', justify='left')
        self.theme.bind_colors(status_label, bg='bg', fg='muted')
        status_label.pack(fill='x', padx=self.px(24), pady=self.px(12))
        status_label.bind('<Configure>', lambda e: configure_changed(status_label, wraplength=max(1, e.width)))
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.build_overview()
        self.refresh_theme()
        self.submit(self.detect_backend, lambda result: self.ready(*result))

    def refresh_theme(self):
        if self.closed:
            return
        future = self.theme_pool.submit(system_theme)
        def poll():
            if self.closed:
                return
            if not future.done():
                self.root.after(15, poll)
                return
            try:
                mode, accent = future.result()
            except Exception:
                mode, accent = self.theme.mode, self.theme.accent
            if (mode, accent) != (self.theme.mode, self.theme.accent):
                self.theme.apply(mode, accent)
            if self.show_after_theme:
                self.show_after_theme = False
                self.root.deiconify()
            self.root.after(2000, self.refresh_theme)
        self.root.after(15, poll)

    def page(self, title):
        return self.scroll_content(self.tab(self.tabs, title))

    def tab(self, notebook, title):
        container = ttk.Frame(notebook, padding=self.px((0, 12, 0, 0)))
        notebook.add(container, text=title)
        return container

    def scroll_content(self, container):
        viewport = ScrollViewport(container, self.root, self.theme, self.px)
        bar = ttk.Scrollbar(container, orient='vertical', command=viewport.yview)
        bar.pack(side='right', fill='y')
        viewport.pack(side='left', fill='both', expand=True)
        viewport.scrollcommand = bar.set
        def wheel(event):
            if str(event.widget).startswith(str(container) + "."):
                viewport.wheel(event)
        for sequence in ('<Button-4>', '<Button-5>', '<MouseWheel>'):
            self.root.bind(sequence, wheel, add='+')
        return viewport.content

    def wrapped_label(self, parent, **kwargs):
        widget = ttk.Label(parent, justify='left', **kwargs)
        self.wrapping.append((widget, parent))
        parent.bind('<Configure>', lambda e: configure_changed(widget,
            wraplength=max(1, e.width - self.px(40))), add='+')
        return widget

    def section(self, page, title, description=None):
        card = ttk.Frame(page, style='Rounded.TFrame', padding=self.px((20, 16)))
        if page == self.overview:
            card.pack(fill='x', pady=self.px((0, 12)))
        ttk.Label(card, text=title, font=self.display.font(12)).pack(anchor='w', pady=self.px((0, 8)))
        if description:
            self.wrapped_label(card, text=description, style='Muted.TLabel',
                               wraplength=self.px(720)).pack(anchor='w', pady=self.px((0, 12)))
        return card

    def heading(self, page, text, top=14):
        ttk.Label(page, text=text, font=self.display.font(12)).pack(anchor='w', pady=self.px((top, 9)))

    def submit(self, fn, done=None, finished=None):
        future = self.pool.submit(fn)
        def poll():
            if self.closed:
                return
            if not future.done():
                self.root.after(80, poll)
                return
            try:
                result = future.result()
                if done:
                    done(result)
            except Exception as error:
                self.status.set(f'失败：{error}')
            finally:
                if finished:
                    finished()
        self.root.after(80, poll)

    @staticmethod
    def detect_backend():
        backend = LinuxBackend()
        return backend, backend.capabilities()

    def build_overview(self):
        grid = ttk.Frame(self.overview)
        grid.pack(fill='x')
        grid.columnconfigure((0, 1), weight=1, uniform='cards')
        summary = self.section(grid, '今天的电脑状态')
        self.metrics = tk.StringVar(value='供电 --  ·  电量 --  ·  模式 --\n充电上限 --  ·  电池功率 --')
        self.wrapped_label(summary, textvariable=self.metrics, font=self.display.font(12),
                           wraplength=self.px(290)).pack(anchor='w', pady=self.px((0, 4)))
        performance = self.section(grid, '运行方式', '安静工作，或是全力发挥，随时切换。')
        row = ttk.Frame(performance, style='Card.TFrame')
        row.pack(fill='x')
        self.profile_buttons = {}
        self.profile_row = row
        for mode in ('quiet', 'balanced', 'performance'):
            self.add_profile_button(mode)
        battery = self.section(grid, '照顾电池', '经常插电使用时，可以适当降低充电上限。')
        row = ttk.Frame(battery, style='Card.TFrame')
        row.pack(fill='x')
        self.limit = tk.IntVar(value='--')
        self.limit_field = ttk.Spinbox(row, from_=20, to=100, textvariable=self.limit, width=6, state='disabled')
        self.limit_field.pack(side='left')
        ttk.Label(row, text=' %   ').pack(side='left')
        self.limit_save = ttk.Button(row, text='保存', width=4, state='disabled', command=self.change_limit)
        self.limit_save.pack(side='left')
        automatic = self.section(grid, '跟随供电，自动调整', '打开后，插上或拔下电源时会切换到你选的运行方式。')
        self.enabled = tk.BooleanVar(value=bool(self.settings.get('enabled', False)))
        ttk.Checkbutton(automatic, text='开启自动切换', variable=self.enabled,
                        command=self.save_policy).pack(anchor='w')
        row = ttk.Frame(automatic, style='Card.TFrame')
        row.pack(fill='x', pady=self.px((8, 0)))
        self.ac = tk.StringVar(value=self.settings.get('ac', 'balanced'))
        self.battery = tk.StringVar(value=self.settings.get('battery', 'quiet'))
        self.policy_modes = []
        self.policy_combos = []
        self.policy_labels = []
        for index, (label, var) in enumerate([('接通电源时', self.ac), ('使用电池时', self.battery)]):
            ttk.Label(row, text=label + '  ').grid(row=index, column=0, sticky='w', pady=self.px(3))
            # Keep hardware identifiers in config, display familiar names in the UI.
            shown = tk.StringVar(value=NAMES.get(var.get(), var.get()))
            self.policy_labels.append(shown)
            combo = ttk.Combobox(row, textvariable=shown,
                                 state='disabled', width=12)
            self.policy_combos.append(combo)
            combo.grid(row=index, column=1, sticky='w', pady=self.px(3))
            def choose(event, c=combo, v=var):
                if not 0 <= c.current() < len(self.policy_modes):
                    return
                v.set(self.policy_modes[c.current()])
                self.save_policy()
            combo.bind('<<ComboboxSelected>>', choose)
        light = self.section(grid, '键盘的光', '选一个舒服的亮度。')
        row = ttk.Frame(light, style='Card.TFrame')
        row.pack(fill='x')
        row.columnconfigure((0, 1), weight=1, uniform='brightness')
        self.keyboard_buttons = []
        for index, (label, level) in enumerate([('关闭', 'off'), ('柔和', 'low'), ('适中', 'med'), ('明亮', 'high')]):
            button = ttk.Button(row, text=label, width=0, padding=self.px((9, 9)),
                                state='disabled', command=lambda l=level: self.perform(lambda: self.backend.set_keyboard(l)))
            self.keyboard_buttons.append(button)
            button.grid(row=index // 2, column=index % 2, sticky='ew',
                        padx=self.px((0, 6) if index % 2 == 0 else (0, 0)),
                        pady=self.px((0, 6) if index < 2 else (0, 0)))
        desktop = self.section(grid, '启动与托盘')
        self.start_at_login = tk.BooleanVar(value=autostart.entry_path().is_file())
        ttk.Checkbutton(desktop, text='登录后自动启动到托盘', variable=self.start_at_login,
                        command=self.save_autostart).pack(anchor='w')
        self.request_admin_at_launch = tk.BooleanVar(value=self.settings.get('request_admin_at_launch') is True)
        ttk.Checkbutton(desktop, text='从快捷方式打开时申请管理员权限',
                        variable=self.request_admin_at_launch).pack(anchor='w')
        self.wrapped_label(desktop, text='收进托盘后继续监测和联动。选择托盘菜单里的「退出」可停止运行。',
                           style='Muted.TLabel').pack(anchor='w', pady=self.px((4, 0)))
        for index, card in enumerate((summary, desktop, performance, battery, automatic, light)):
            card.grid(row=index // 2, column=index % 2, sticky='nsew',
                      padx=self.px((0, 6) if index % 2 == 0 else (6, 0)), pady=self.px((0, 12)))

    def add_profile_button(self, mode):
        button = ttk.Button(self.profile_row, text=NAMES.get(mode, mode), width=4, state='disabled',
                            command=lambda: self.change_profile(mode))
        button.pack(side='left', padx=self.px((0, 10)))
        self.profile_buttons[mode] = button

    def ready(self, backend, caps=None):
        self.backend = backend
        if caps is None:
            caps = backend.capabilities()
        self.model.config(text=caps['model'].split('_')[0] + '  ·  按你的习惯，照顾电脑的每一天')
        self.policy_modes = caps['profiles']
        for mode in self.policy_modes:
            if mode not in self.profile_buttons:
                self.add_profile_button(mode)
        for mode, button in self.profile_buttons.items():
            if mode not in self.policy_modes:
                button.pack_forget()
            elif caps['modern_cli']:
                button.configure(state='normal')
        for combo in self.policy_combos:
            combo.configure(values=[NAMES.get(mode, mode) for mode in self.policy_modes],
                            state='readonly' if self.policy_modes else 'disabled')
        limit = caps.get('battery_info', {}).get('limit_percent')
        if limit is not None:
            self.limit.set(int(limit))
        if caps['charge_limit'] and caps['modern_cli']:
            self.limit_field.configure(state='normal')
            self.limit_save.configure(state='normal')
        if caps['keyboard'] and caps['modern_cli']:
            for button in self.keyboard_buttons:
                button.configure(state='normal')
        self.page_capabilities = caps
        self.latest_state = {}
        self.lazy_pages = {
            str(self.battery_page.master.master): self.build_battery_page,
            str(self.advanced.master.master): lambda: self.build_fans(caps),
            str(self.combination_page): self.build_combination_page,
        }
        self.tabs.bind('<<NotebookTabChanged>>', self.load_selected_page, add='+')
        self.load_selected_page()
        self.status.set('准备好了 · 自动切换在程序运行期间生效，包括托盘')
        self.tick()

    def load_selected_page(self, event=None):
        build = self.lazy_pages.pop(self.tabs.select(), None)
        if build is not None:
            build()
        self.refresh_open_pages(self.latest_state)

    def build_battery_page(self):
        caps = self.page_capabilities
        self.battery_panel = BatteryPanel(self, self.battery_page, caps['charge_limit'] and caps['modern_cli'])
        self.battery_panel.refresh(caps.get('battery_info'))

    def build_combination_page(self):
        caps = self.page_capabilities
        self.combination_panel = CombinationPanel(self, self.combination_page, caps, self.combination_settings)
        self.build_profile_epp(self.combination_panel.epp_card, caps.get('profile_epp', {}))
        if self.combination_error:
            self.combination_panel.note.set('已停用无效的组合配置，请修正后重新保存：' + self.combination_error)

    def open_fan_settings(self):
        self.tabs.select(self.combination_page)
        self.load_selected_page()
        self.open_combination_section(self.combination_panel.fan_card)

    def refresh_open_pages(self, state):
        if self.tabs.select() == str(self.battery_page.master.master) and hasattr(self, 'battery_panel'):
            self.battery_panel.refresh(state.get('battery_info', self.page_capabilities.get('battery_info')))
        if self.tabs.select() == str(self.advanced.master.master) and hasattr(self, 'sensor_groups'):
            self.update_sensors(state.get('sensors', {}))
            self.refresh_igpu_controls(state.get('igpu_clock'))
        if self.tabs.select() == str(self.combination_page) and hasattr(self, 'combination_panel'):
            self.combination_panel.refresh(state)
            self.combination_panel.refresh_power(state.get('power_limits', self.page_capabilities.get('power_limits')))
            cpu = state.get('cpu_epp', {})
            if cpu:
                text = ('实际 EPP：' + '、'.join(cpu.get('energy_performance_preference', []))
                        + '  ·  调速器：' + '、'.join(cpu.get('scaling_governor', [])))
                if self.epp_live.get() != text:
                    self.epp_live.set(text)

    def perform(self, fn):
        self.status.set('正在保存你的设置…')
        self.submit(fn, lambda result: self.status.set('已保存' + (f'：{result}' if result else '')))

    def change_profile(self, mode):
        self.perform(lambda: self.backend.set_profile(mode))

    def change_limit(self):
        try:
            value = self.limit.get()
        except tk.TclError:
            self.status.set('请输入 20–100 的整数')
            return
        self.perform(lambda: self.backend.set_limit(value))

    def save_autostart(self):
        enabled = self.start_at_login.get()
        try:
            autostart.set_enabled(enabled)
        except (OSError, ValueError, KeyError) as error:
            self.start_at_login.set(autostart.entry_path().is_file())
            self.status.set(f'自启动设置保存失败：{error}')
            return
        self.status.set('已开启登录后启动到托盘' if enabled else '已关闭登录自启动')

    def save_policy(self):
        self.settings.update({'enabled': self.enabled.get(), 'ac': self.ac.get(), 'battery': self.battery.get()})
        try:
            save(self.settings)
        except OSError as error:
            self.status.set(f'配置保存失败：{error}')
            return
        self.status.set('偏好已保存 · 下次供电变化时自动切换')

    def save_combinations(self, settings):
        settings = normalized(settings)
        proposed = dict(self.settings, combinations=settings)
        save(proposed)
        self.settings = proposed
        self.combination_settings = settings
        self.combination_policy.previous = None
        self.status.set('六套组合配置已保存 · 收进托盘后继续联动')

    def update_combinations(self, state):
        request = self.combination_policy.request(state, self.combination_settings)
        if request:
            mode, source, entry = request
            if entry['power'] or entry['fans']:
                self.status.set('正在应用当前供电与运行方式的组合…')
                def done(message):
                    if hasattr(self, 'combination_panel'):
                        self.combination_panel.note.set(message)
                    self.status.set(message)
                self.submit(lambda: self.backend.apply_combination(mode, source, entry), done)
    def tick(self):
        if self.closed:
            return
        self.submit(self.backend.snapshot, self.update)

    def update(self, state):
        def fmt(value, suffix=''):
            return '--' if value is None else f'{value:g}{suffix}'
        power = '外接电源' if state['ac'] is True else '电池供电' if state['ac'] is False else '供电 --'
        self.metrics.set(f'{power}    ·    电量 {fmt(state["battery"], "%")}    ·    {NAMES.get(state['profile'], state['profile']) or '模式 --'}\n'
                         f'充电上限 {fmt(state["limit"], "%")}    ·    电池功率 {fmt(state["battery_watts"], " W")}')
        if self.root.globalgetvar(str(self.limit)) == '--' and state['limit'] is not None:
            self.limit.set(int(state['limit']))
        self.latest_state = state
        self.refresh_open_pages(state)
        ac, profile = state['ac'], state['profile']
        target = self.policy.target(ac, self.settings)
        if target and target != profile and self.backend.modern:
            self.perform(lambda: self.backend.switch_profile_on_power_change(target, profile, ac))
        else:
            self.update_combinations(state)
        self.root.after(3000, self.tick)

    def build_profile_epp(self, parent, info):
        self.epp_live = tk.StringVar(value='正在读取实际 EPP…')
        self.wrapped_label(parent, textvariable=self.epp_live, style='Muted.TLabel').pack(anchor='w')
        if not info.get('supported'):
            self.wrapped_label(parent, text='当前 asusd 未提供 EPP 联动接口。', style='Muted.TLabel').pack(anchor='w')
            return
        card = parent
        self.wrapped_label(card, text='规则由 asusd 持续管理。系统电源服务或动态 EPP 监控也可能改写实际值。',
                           style='Muted.TLabel').pack(anchor='w', pady=self.px((4, 8)))
        if info.get('ppd_active'):
            self.wrapped_label(card, text='检测到 power-profiles-daemon 正在运行，可能覆盖方案的 EPP。保存后请核对实际值。',
                               style='Muted.TLabel').pack(anchor='w', pady=self.px((0, 8)))
        self.epp_linked = tk.BooleanVar(value=info.get('enabled', False))
        ttk.Checkbutton(card, text='随方案切换 EPP', variable=self.epp_linked).pack(anchor='w')
        self.epp_variables = {}
        choices = info.get('choices', [])
        row = ttk.Frame(card, style='Card.TFrame')
        row.pack(fill='x', pady=self.px(8))
        row.columnconfigure((0, 1, 2), weight=1, uniform='epp_modes')
        for index, profile in enumerate(('quiet', 'balanced', 'performance')):
            spacing = self.px((0, 12) if index < 2 else (0, 0))
            ttk.Label(row, text=NAMES[profile]).grid(
                row=0, column=index, sticky='w', padx=spacing, pady=self.px((0, 5)))
            selected = info.get('profiles', {}).get(profile, 'default')
            variable = tk.StringVar(value=EPP_NAMES.get(selected, selected))
            self.epp_variables[profile] = variable
            ttk.Combobox(row, textvariable=variable, values=[EPP_NAMES[value] for value in choices],
                         state='readonly', width=10).grid(
                row=1, column=index, sticky='ew', padx=spacing)
        self.epp_save = ttk.Button(card, text='保存 asusd 规则',
                                   command=self.save_profile_epp)
        self.epp_save.pack(anchor='w')

    def open_combination_section(self, section):
        self.tabs.select(self.combination_page)
        self.combination_panel.sections.select(section.master.master.master)

    def save_profile_epp(self):
        names = {label: value for value, label in EPP_NAMES.items()}
        profiles = {key: names[variable.get()] for key, variable in self.epp_variables.items()}
        enabled = self.epp_linked.get()
        self.epp_save.configure(state='disabled')
        self.status.set('正在保存 EPP 方案规则…')
        self.submit(lambda: self.backend.save_profile_epp(enabled, profiles),
                    lambda result: self.status.set(result),
                    finished=lambda: self.epp_save.configure(state='normal'))

    def build_fans(self, caps):
        self.heading(self.advanced, '实时温度与转速', top=0)
        ttk.Button(self.advanced, text='前往组合策略编辑风扇曲线', width=0,
                   command=self.open_fan_settings).pack(
                       anchor='w', pady=self.px((0, 12)))
        readings = ttk.Frame(self.advanced, style='Card.TFrame')
        readings.pack(fill='x')
        readings.columnconfigure((0, 1), weight=1, uniform='readings')
        self.sensor_groups = {}
        for column, (unit, title) in enumerate([('°C', '温度（°C）'), ('RPM', '风扇转速（RPM）')]):
            group = ttk.Frame(readings, style='Card.TFrame')
            group.grid(row=0, column=column, sticky='new',
                       padx=self.px((0, 12) if column == 0 else (12, 0)))
            group.columnconfigure(0, weight=1)
            group.columnconfigure(1, minsize=self.px(66))
            ttk.Label(group, text=title, style='Muted.TLabel').grid(
                row=0, column=0, columnspan=2, sticky='w', pady=self.px((0, 5)))
            status = ttk.Label(group, text='正在读取…', style='Muted.TLabel')
            status.grid(row=1, column=0, columnspan=2, sticky='w')
            self.sensor_groups[unit] = (group, status, {})

            def reflow(event, rows=self.sensor_groups[unit][2]):
                for name, _ in rows.values():
                    configure_changed(name, wraplength=max(1, event.width - self.px(78)))

            group.bind('<Configure>', reflow)
        self.build_igpu(caps.get('igpu_clock', {'supported': False, 'reason': '未检测到核显频率控制接口'}))

    def build_igpu(self, info):
        self.heading(self.advanced, '核显最高频率')
        self.igpu_status = tk.StringVar()
        self.wrapped_label(self.advanced, textvariable=self.igpu_status,
                           style='Muted.TLabel').pack(anchor='w')
        self.wrapped_label(self.advanced, text='降低最高频率可减少核显高负载时的功耗。安装组件、应用上限或恢复自动调频时会请求管理员授权。',
                           style='Muted.TLabel').pack(anchor='w', pady=self.px((4, 8)))
        row = ttk.Frame(self.advanced, style='Card.TFrame')
        row.pack(fill='x')
        self.igpu_max = tk.IntVar(value=info.get('maximum', 0))
        self.igpu_input = ttk.Spinbox(row, from_=info.get('minimum', 0), to=info.get('upper', 0),
                                     increment=50, textvariable=self.igpu_max, width=6)
        self.igpu_input.pack(side='left')
        ttk.Label(row, text=' MHz  ').pack(side='left')
        self.igpu_apply = ttk.Button(row, text='应用', width=0, command=self.change_igpu)
        self.igpu_apply.pack(side='left', padx=self.px((0, 8)))
        self.igpu_reset = ttk.Button(row, text='恢复自动调频', width=0,
                                     command=lambda: self.change_igpu(reset=True))
        self.igpu_reset.pack(side='left')
        self.igpu_install = ttk.Button(self.advanced, text='安装核显控制组件…',
                                       command=lambda: self.change_igpu(install=True))
        self.igpu_install.pack(anchor='w', pady=self.px((8, 0)))
        self.igpu_busy = False
        self.refresh_igpu_controls(info)

    def refresh_igpu_controls(self, info=None):
        if info is not None:
            self.igpu_info = info
        info = self.igpu_info
        supported = info.get('supported', False)
        ready = supported and info.get('helper_ready', False)
        if supported:
            frequency = info['frequency_mhz']
            current = '—' if frequency is None else f'{frequency:.0f} MHz'
            mode = {'auto': '自动', 'manual': '手动上限'}.get(info['mode'], info['mode'])
            self.igpu_status.set(f"当前 {current} · 上限 {info['maximum']} MHz · {mode}\n"
                                 f"可调范围 {info['minimum']}–{info['upper']} MHz")
            self.igpu_input.configure(from_=info['minimum'], to=info['upper'])
        else:
            self.igpu_status.set(info.get('reason', '核显频率控制不可用'))
        for control in (self.igpu_input, self.igpu_apply, self.igpu_reset):
            control.configure(state='normal' if ready and not self.igpu_busy else 'disabled')
        if ready or not supported:
            self.igpu_install.pack_forget()
        else:
            self.igpu_install.pack(anchor='w', pady=self.px((8, 0)))
            self.igpu_install.configure(state='disabled' if self.igpu_busy else 'normal')

    def change_igpu(self, reset=False, install=False):
        if self.igpu_busy:
            return
        if install:
            action = self.backend.install_igpu_helper
        else:
            try:
                maximum = None if reset else self.igpu_max.get()
            except tk.TclError:
                self.status.set('请输入整数 MHz')
                return
            action = lambda: self.backend.set_igpu_max(maximum)
        self.igpu_busy = True
        self.refresh_igpu_controls()
        self.status.set('请在系统授权窗口中确认核显操作…')

        def refreshed(info):
            if info.get('supported'):
                self.igpu_max.set(info['maximum'])
            self.refresh_igpu_controls(info)

        def release():
            self.igpu_busy = False
            self.refresh_igpu_controls()

        def complete():
            self.submit(self.backend.igpu_clock, refreshed, finished=release)

        self.submit(action, lambda result: self.status.set(result), finished=complete)

    def update_sensors(self, sensors):
        readings = {'°C': {}, 'RPM': {}}
        for name, value in sensors.items():
            if name.endswith(' RPM'):
                readings['RPM'][name.removesuffix(' RPM')] = f'{value:g}'
            else:
                readings['°C'][name] = f'{value:.1f}'
        for unit, values in readings.items():
            group, status, rows = self.sensor_groups[unit]
            status.configure(text='未检测到读数')
            if values:
                status.grid_remove()
            else:
                status.grid()
            if rows.keys() != values.keys():
                for labels in rows.values():
                    for label in labels:
                        label.destroy()
                rows.clear()
                width = group.winfo_width()
                wrap = width - self.px(78) if width > self.px(78) else self.px(220)
                for index, name in enumerate(values, start=1):
                    label = ttk.Label(group, text=name, wraplength=wrap, justify='left')
                    label.grid(row=index, column=0, sticky='ew',
                               padx=self.px((0, 12)), pady=self.px(3))
                    value = ttk.Label(group, anchor='e')
                    value.grid(row=index, column=1, sticky='ew', pady=self.px(3))
                    rows[name] = (label, value)
            for name, value in values.items():
                configure_changed(rows[name][1], text=value)

    def close(self):
        preference = getattr(self, 'request_admin_at_launch', None)
        if preference is not None and preference.get() != (self.settings.get('request_admin_at_launch') is True):
            try:
                save(dict(self.settings, request_admin_at_launch=preference.get()))
            except OSError as error:
                messagebox.showerror('未能保存启动授权设置', str(error), parent=self.root)
        self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.theme_pool.shutdown(wait=False, cancel_futures=True)
        for callback in self.root.tk.call('after', 'info'):
            self.root.after_cancel(callback)
        # Release Tk images on the UI thread while the interpreter is alive.
        self.theme.images.clear()
        self.theme.bindings.clear()
        self.root.destroy()


def launch(start_hidden=False):
    from pathlib import Path
    from .instance import Instance
    from .tray import create_tray

    instance = Instance(start_hidden)
    if not instance.acquire():
        return
    try:
        root = tk.Tk(className='AsusPowerController')
        root.withdraw()
        icon_path = Path('/usr/share/icons/Yaru/48x48/categories/preferences-system-power.png')
        if icon_path.is_file():
            root.iconphoto(True, tk.PhotoImage(master=root, file=str(icon_path)))
        app = Application(root)
        run_window(app, instance, create_tray, start_hidden)
    finally:
        instance.close()


def run_window(app, instance, create_tray, start_hidden):
    root = app.root
    tray = None

    def show():
        app.show_after_theme = False
        root.deiconify()
        root.lift()

    def quit_app():
        if tray is not None:
            tray.close()
        app.close()

    def close_window():
        if tray is not None and tray.connected:
            app.show_after_theme = False
            root.withdraw()
        else:
            quit_app()

    def start_tray():
        nonlocal tray
        try:
            tray = create_tray(root, show, quit_app)
        except (OSError, AttributeError, RuntimeError, ValueError):
            pass
        if start_hidden:
            root.after(2500 if tray else 0, reveal_if_no_tray)

    def reveal_if_no_tray():
        if tray is None or not tray.connected:
            show()

    instance.attach(root, show, quit_app)
    root.protocol('WM_DELETE_WINDOW', close_window)
    if start_hidden:
        app.show_after_theme = False
        root.after_idle(start_tray)
    else:
        def on_map(event):
            if event.widget is root:
                root.unbind('<Map>', map_binding)
                root.after(120, start_tray)
        map_binding = root.bind('<Map>', on_map, add='+')
    root.mainloop()
    app.pool.shutdown(wait=True, cancel_futures=True)
