"""Power settings with firmware ranges and explicit application controls."""
import tkinter as tk
from tkinter import ttk, messagebox


class PowerPanel:
    def __init__(self, app, parent, state, profile_names):
        self.app, self.parent = app, parent
        self.profile_names = profile_names
        self.px = app.px
        self.busy = False
        self.rows = {}
        self.state = {}
        self.enabled = tk.BooleanVar()
        self.heading = tk.StringVar()
        app.heading(parent, '功率设置', top=0)
        app.wrapped_label(parent, textvariable=self.heading, style='Muted.TLabel').pack(anchor='w')
        self.toggle = ttk.Checkbutton(parent, text='启用当前方案的功率调节', variable=self.enabled,
                                     command=self.toggle_tuning)
        self.toggle.pack(anchor='w', pady=self.px((10, 4)))
        app.wrapped_label(parent, text='启用会应用 asusd 为当前性能方案和供电状态保存的功率配置。设置效果受固件、温度和供电条件影响。',
                          style='Muted.TLabel').pack(anchor='w')
        self.note = tk.StringVar()
        app.wrapped_label(parent, textvariable=self.note, style='Muted.TLabel').pack(
            anchor='w', pady=self.px((6, 10)))
        self.fields = ttk.Frame(parent, style='Card.TFrame')
        self.fields.pack(fill='x')
        self.refresh(state)

    def refresh(self, state):
        if state is None or self.busy or state == self.state:
            return
        context_changed = state.get('context') != self.state.get('context')
        rebuild = set(state.get('attributes', {})) != set(self.rows)
        self.state = state
        self.enabled.set(state.get('enabled', False))
        if state.get('supported'):
            profile = self.profile_names.get(state['context']['profile'], state['context']['profile'])
            self.heading.set(f'当前性能方案 · {profile}。范围和默认值由固件提供。')
        else:
            self.heading.set(state.get('reason', '功率接口不可用'))
        if rebuild:
            for child in self.fields.winfo_children():
                child.destroy()
            self.rows.clear()
            for name, data in state.get('attributes', {}).items():
                group = ttk.Frame(self.fields, style='Card.TFrame')
                group.pack(fill='x', pady=self.px((8, 12)))
                self.app.wrapped_label(group, text=data['label']).pack(anchor='w')
                detail = tk.StringVar()
                self.app.wrapped_label(group, textvariable=detail, style='Muted.TLabel').pack(
                    anchor='w', pady=self.px((3, 6)))
                row = ttk.Frame(group, style='Card.TFrame')
                row.pack(fill='x')
                value = tk.IntVar(value=data['current'])
                field = ttk.Spinbox(row, textvariable=value, width=6)
                field.pack(side='left')
                ttk.Label(row, text=' W  ').pack(side='left')
                apply = ttk.Button(row, text='应用', width=0, command=lambda key=name: self.apply(key))
                apply.pack(side='left', padx=self.px((0, 8)))
                reset = ttk.Button(row, text='恢复默认值', width=0, command=lambda key=name: self.apply(key, reset=True))
                reset.pack(side='left')
                self.rows[name] = dict(value=value, detail=detail, field=field, apply=apply, reset=reset)
        for name, row in self.rows.items():
            data = state['attributes'][name]
            if context_changed:
                row['value'].set(data['current'])
            row['field'].configure(from_=data['minimum'], to=data['maximum'], increment=data['step'])
            default = '—' if data['default'] is None else f"{data['default']} W"
            detail = (f"实际当前 {data['current']} W · 范围 {data['minimum']}–{data['maximum']} W"
                      f" · 默认 {default}")
            if data.get('pending') is not None:
                detail += f" · 待应用 {data['pending']} W"
            row['detail'].set(detail)
        self.set_controls()
        if context_changed and not rebuild:
            self.note.set('电源或性能方案已改变，已读取当前配置。')

    def set_controls(self):
        supported = self.state.get('supported', False)
        self.toggle.configure(state='normal' if supported and not self.busy else 'disabled')
        active = supported and self.state.get('enabled') and not self.busy
        for name, row in self.rows.items():
            for key in ('field', 'apply', 'reset'):
                available = active and (key != 'reset' or self.state['attributes'][name]['default'] is not None)
                row[key].configure(state='normal' if available else 'disabled')

    def run(self, action):
        if self.busy:
            return
        self.busy = True
        self.set_controls()
        self.note.set('正在应用并检查驱动读数…')
        self.app.status.set('正在应用功率设置…')

        def success(message):
            self.note.set(message)
            self.app.status.set(message)

        def refreshed(state):
            self.busy = False
            self.refresh(state)

        def release():
            self.busy = False
            self.set_controls()

        def finish():
            if self.note.get().startswith('正在'):
                self.note.set(self.app.status.get())
            self.app.submit(self.app.backend.power_limits, refreshed, finished=release)

        self.app.submit(action, success, finished=finish)

    def toggle_tuning(self):
        enabled = self.enabled.get()
        self.run(lambda: self.app.backend.set_power_tuning(enabled))

    def apply(self, name, reset=False):
        if reset and not messagebox.askokcancel('恢复功率默认值',
            f"将「{self.state['attributes'][name]['label']}」恢复为固件默认值，继续？", parent=self.app.root):
            return
        try:
            value = None if reset else self.rows[name]['value'].get()
        except tk.TclError:
            self.note.set('请输入整数瓦数')
            return
        self.run(lambda: self.app.backend.set_power_limit(name, value, reset))
