"""Editor for six independent combinations, with offline drafts and EPP presets."""
from copy import deepcopy
import tkinter as tk
from tkinter import messagebox, ttk
from .combinations import MODES, SOURCES, normalized, validate, effective
from .fan_editor import FanEditor

MODE_NAMES = {'quiet': '省电', 'balanced': '均衡', 'performance': '性能'}
SOURCE_NAMES = {'ac': '插电', 'battery': '离电'}


class CombinationPanel:
    def __init__(self, app, parent, caps, settings):
        self.app, self.caps = app, caps
        self.draft = normalized(settings)
        self.loaded = None
        self.busy = False
        self.applying = False
        self.generation = 0
        self.templates = {}
        fans = caps.get('fan_curves', {})
        if fans.get('supported'):
            self.templates[fans['initial_profile']] = deepcopy(fans['curves'])
        self.note = tk.StringVar(value='')
        card = ttk.Frame(parent, padding=app.px((16, 10)), style='Rounded.TFrame')
        card.pack(fill='x', pady=app.px((0, 14)))
        self.enabled = tk.BooleanVar(value=self.draft['enabled'])
        self.live = tk.StringVar(value='等待实际运行状态…')
        row = ttk.Frame(card, style='Card.TFrame')
        row.pack(fill='x')
        self.mode = tk.StringVar(value='均衡')
        self.source = tk.StringVar(value='插电')
        self.mode_combo = ttk.Combobox(row, textvariable=self.mode, values=list(MODE_NAMES.values()), state='readonly', width=8)
        self.source_combo = ttk.Combobox(row, textvariable=self.source, values=list(SOURCE_NAMES.values()), state='readonly', width=8)
        ttk.Label(row, text='编辑组合', style='Muted.TLabel').pack(side='left', padx=app.px((0, 10)))
        self.mode_combo.pack(side='left', padx=app.px((0, 8)))
        self.source_combo.pack(side='left')
        for combo in (self.mode_combo, self.source_combo):
            combo.bind('<<ComboboxSelected>>', self.switch)
        for label, command in [('应用此组合', self.apply), ('保存全部配置', self.save)]:
            ttk.Button(row, text=label, width=0, command=command).pack(side='right', padx=app.px((6, 0)))
        options = ttk.Frame(card, style='Card.TFrame')
        options.pack(fill='x', pady=app.px((6, 0)))
        ttk.Checkbutton(options, text='自动联动（保存后生效）', variable=self.enabled).pack(side='left')
        ttk.Label(options, textvariable=self.live, style='Muted.TLabel').pack(side='right')
        feedback = app.wrapped_label(card, textvariable=self.note, style='Muted.TLabel')
        def show_feedback(*args):
            if self.note.get():
                feedback.pack(anchor='w', pady=app.px((4, 0)))
            else:
                feedback.pack_forget()
        self.note.trace_add('write', show_feedback)
        self.sections = ttk.Notebook(parent, style='Sections.TNotebook')
        self.sections.pack(fill='both', expand=True)

        epp = app.section(app.scroll_content(app.tab(self.sections, 'EPP')), 'asusd 模式联动',
                          '按静音、均衡和性能模式选择 EPP 档位。插电和离电共享同一套规则。')
        epp.pack(fill='x')
        self.epp_card = epp

        power = app.section(app.scroll_content(app.tab(self.sections, '功耗')), '此组合的功耗', '勾选需要接管的功耗项；未勾选的项目保留当前值。范围显示当前固件读数。')
        power.pack(fill='x', pady=app.px((0, 12)))
        self.power_rows = {}
        self.power_readings = {}
        for name, data in caps.get('power_limits', {}).get('attributes', {}).items():
            group = ttk.Frame(power, style='Card.TFrame')
            group.pack(fill='x', pady=app.px(6))
            enabled = tk.BooleanVar(value=False)
            variable = tk.IntVar(value=data['current'])
            ttk.Checkbutton(group, text=data['label'], variable=enabled).pack(side='left')
            row = ttk.Frame(group, style='Card.TFrame')
            row.pack(side='right')
            field = ttk.Spinbox(row, from_=data['minimum'], to=data['maximum'], increment=data['step'], width=6, textvariable=variable, state='disabled')
            field.pack(side='left')
            enabled.trace_add('write', lambda *args, widget=field, checked=enabled: widget.configure(
                state='normal' if checked.get() else 'disabled'))
            ttk.Label(row, text=' W').pack(side='left')
            default = ttk.Button(row, text='填入默认值', width=0, padding=app.px((6, 4)),
                                 command=lambda key=name: self.fill_power_default(key))
            default.pack(side='left', padx=app.px(6))
            reading = ttk.Label(power, style='Muted.TLabel')
            reading.pack(anchor='w', pady=app.px((0, 4)))
            self.power_readings[name] = (field, reading, default)
            self.power_rows[name] = (enabled, variable)
        self.power_state = {}
        self.refresh_power(caps.get('power_limits'))
        if not self.power_rows:
            app.wrapped_label(power, text='当前固件未提供可读取的功耗参数。', style='Muted.TLabel').pack(anchor='w')

        fan_card = app.section(app.scroll_content(app.tab(self.sections, '风扇')), '此组合的风扇曲线')
        self.fan_card = fan_card
        fan_card.pack(fill='x', pady=app.px((0, 12)))
        self.fan_enabled = tk.BooleanVar(value=False)
        fan_actions = ttk.Frame(fan_card, style='Card.TFrame')
        fan_actions.pack(fill='x', pady=app.px((0, 6)))
        ttk.Checkbutton(fan_actions, text='管理风扇曲线', variable=self.fan_enabled).pack(side='left')
        ttk.Button(fan_actions, text='读取方案曲线', width=0, command=self.read_fans).pack(side='left', padx=app.px(8))
        ttk.Button(fan_actions, text='复制到另一种供电', width=0, command=self.copy_fans).pack(side='left')
        self.copy_note = tk.StringVar()
        app.wrapped_label(fan_card, textvariable=self.copy_note, style='Muted.TLabel').pack(anchor='w')
        self.editor = None
        if fans.get('supported'):
            self.editor = FanEditor(app, fan_card, {'supported': False, 'reason': '请读取所选方案的曲线。'}, MODE_NAMES, draft=True)
        else:
            app.wrapped_label(fan_card, text=fans.get('reason', '当前设备没有可读取的风扇曲线接口。'), style='Muted.TLabel').pack(anchor='w')
        self.fan_enabled.trace_add('write', self.sync_fan_state)
        self.load(('balanced', 'ac'))

    def sync_fan_state(self, *args):
        if self.editor is not None:
            self.editor.set_busy(self.busy or not self.fan_enabled.get() or self.editor.curve is None)

    def selection(self):
        return (next(key for key, value in MODE_NAMES.items() if value == self.mode.get()),
                next(key for key, value in SOURCE_NAMES.items() if value == self.source.get()))

    def collect(self):
        if self.loaded is None:
            return
        mode, source = self.loaded
        entry = deepcopy(self.draft['entries'][mode][source])
        # Preserve stored attributes unsupported by this machine instead of dropping them.
        for name, (enabled, variable) in self.power_rows.items():
            if enabled.get():
                entry['power'][name] = variable.get()
            else:
                entry['power'].pop(name, None)
        if self.fan_enabled.get():
            if self.editor is None:
                raise ValueError('当前设备没有可编辑的风扇曲线')
            entry['fans'] = self.editor.export_draft()
        else:
            entry['fans'] = {}
        proposed = deepcopy(self.draft)
        proposed['entries'][mode][source] = entry
        proposed['enabled'] = self.enabled.get()
        validate(proposed)
        self.draft = proposed

    def switch(self, event=None):
        selected = self.selection()
        try:
            self.collect()
        except (ValueError, tk.TclError) as error:
            self.note.set(f'请先修正当前组合：{error}')
            self.mode.set(MODE_NAMES[self.loaded[0]])
            self.source.set(SOURCE_NAMES[self.loaded[1]])
            return
        self.load(selected)

    def load(self, selected):
        self.generation += 1
        self.loaded = selected
        self.copy_note.set('')
        mode, source = selected
        entry = self.draft['entries'][mode][source]
        self.fan_enabled.set(bool(entry['fans']))
        for name, (enabled, variable) in self.power_rows.items():
            enabled.set(name in entry['power'])
            if name in entry['power']:
                variable.set(entry['power'][name])
        if self.editor is not None:
            curves = entry['fans'] or self.templates.get(mode)
            if curves:
                self.editor.set_busy(False)
                self.editor.show_profile(mode, deepcopy(curves))
                self.editor.note.set('勾选风扇管理后可编辑；此处只修改草稿，保存全部配置后才会用于联动。')
                self.sync_fan_state()
            else:
                self.editor.curve = None
                self.editor.set_busy(True)
                self.editor.note.set('请点击「读取方案曲线」，读取不会改变当前模式。')
                self.editor.draw()
        self.note.set('')

    def copy_fans(self):
        if not self.fan_enabled.get() or self.editor is None:
            self.copy_note.set('请先读取曲线并勾选此组合的风扇管理。')
            return
        mode, source = self.loaded
        target = 'battery' if source == 'ac' else 'ac'
        destination = self.draft['entries'][mode][target]
        try:
            curves = self.editor.export_draft()
        except (ValueError, tk.TclError) as error:
            self.copy_note.set(str(error))
            return
        name = f'{MODE_NAMES[mode]} · {SOURCE_NAMES[target]}'
        if destination['fans'] and not messagebox.askokcancel(
                '覆盖风扇曲线', f'用当前曲线替换「{name}」的全部风扇曲线？',
                parent=self.app.root):
            return
        destination['fans'] = deepcopy(curves)
        self.copy_note.set(f'已复制到「{name}」的草稿。请点击「保存全部配置」保留。')

    def read_fans(self):
        if self.editor is None or self.busy:
            return
        if not self.editor.discard():
            return
        mode, source = self.loaded
        generation = self.generation
        self.busy = True
        self.editor.set_busy(True)
        self.note.set('正在读取所选方案的风扇曲线…')
        def done(curves):
            self.templates[mode] = deepcopy(curves)
            if generation == self.generation:
                self.editor.show_profile(mode, deepcopy(curves))
                self.note.set('已读取方案曲线，可编辑并保存到所选组合。')
        def finish():
            self.busy = False
            self.sync_fan_state()
            if generation == self.generation:
                if self.note.get().startswith('正在读取'):
                    self.note.set(self.app.status.get())
        self.app.submit(lambda: self.app.backend.read_fan_curves(mode), done, finished=finish)

    def save(self):
        try:
            self.collect()
            self.app.save_combinations(deepcopy(self.draft))
            self.note.set('六套配置已保存。启用联动时将在下一次状态刷新应用当前组合。')
        except (ValueError, OSError, tk.TclError) as error:
            self.note.set(f'保存失败：{error}')

    def apply(self):
        if self.applying:
            return
        try:
            self.collect()
            mode, source = self.loaded
            entry = effective(self.draft, mode, source)
        except (ValueError, tk.TclError) as error:
            self.note.set(f'组合无效：{error}')
            return
        self.applying = True
        self.app.status.set('正在应用组合并检查硬件读数…')
        self.app.submit(lambda: self.app.backend.apply_combination(mode, source, entry),
                        lambda message: (self.note.set(message + '；如需下次保留，请保存全部配置。'), self.app.status.set(message)),
                        finished=lambda: setattr(self, 'applying', False))

    def fill_power_default(self, name):
        value = self.power_state.get(name, {}).get('default')
        if value is not None:
            enabled, variable = self.power_rows[name]
            variable.set(value)
            enabled.set(True)

    def refresh_power(self, state):
        attributes = (state or {}).get('attributes', {})
        for name, (field, reading, default) in self.power_readings.items():
            data = attributes.get(name, {})
            if data == self.power_state.get(name):
                continue
            if data:
                field.configure(from_=data['minimum'], to=data['maximum'], increment=data['step'])
                text = f"实际 {data['current']} W · 范围 {data['minimum']}–{data['maximum']} W · 步长 {data['step']} W"
                if data.get('pending') is not None:
                    text += f" · 待应用 {data['pending']} W"
                if data.get('default') is not None:
                    text += f" · 默认 {data['default']} W"
            else:
                text = '当前读数不可用'
            reading.configure(text=text)
            default.configure(state='normal' if data.get('default') is not None else 'disabled')
        self.power_state = attributes

    def refresh(self, state):
        mode = MODE_NAMES.get(state.get('profile'), state.get('profile') or '未知模式')
        source = '插电' if state.get('ac') is True else '离电' if state.get('ac') is False else '供电未知'
        text = f'当前实际组合：{mode} · {source}'
        if self.live.get() != text:
            self.live.set(text)
