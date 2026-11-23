"""Battery information cards using the application's existing rounded theme."""
import tkinter as tk
from tkinter import ttk

from .battery import formatted_values


class BatteryPanel:
    def __init__(self, app, parent, supported):
        self.app = app
        self.variables = {}
        self.last_values = {}
        self.status_card = app.section(parent, '电池状态')
        self.status_card.pack(fill='x', pady=app.px((0, 12)))
        self.label(self.status_card, 'summary', muted=True).pack(anchor='w', pady=app.px((0, 12)))
        metrics = ttk.Frame(self.status_card, style='Card.TFrame')
        metrics.pack(fill='x')
        metrics.columnconfigure((0, 1), weight=1, uniform='battery_metrics')
        for index, (title, key) in enumerate((('当前电量', 'percent'), ('电池健康度', 'health'),
                                            ('电池功率', 'power'), ('预计剩余时间', 'time'))):
            cell = ttk.Frame(metrics, style='Card.TFrame')
            cell.grid(row=index // 2, column=index % 2, sticky='nsew', pady=app.px((0, 12)))
            if key in ('power', 'time'):
                self.label(cell, key + '_title', muted=True).pack(anchor='w')
            else:
                ttk.Label(cell, text=title, style='Muted.TLabel').pack(anchor='w')
            self.label(cell, key, font=app.display.font(15)).pack(anchor='w', pady=app.px((4, 0)))
        self.label(self.status_card, 'note', muted=True).pack(anchor='w')
        charge = app.section(parent, '充电上限', '经常插电时可适当降低上限；预计充电时间以此上限为目标。')
        charge.pack(fill='x', pady=app.px((0, 12)))
        row = ttk.Frame(charge, style='Card.TFrame')
        row.pack(fill='x')
        ttk.Label(row, text='实际上限：').pack(side='left')
        self.label(row, 'limit').pack(side='left', padx=app.px((0, 18)))
        state = 'normal' if supported else 'disabled'
        ttk.Spinbox(row, from_=20, to=100, textvariable=app.limit, width=6, state=state).pack(side='left')
        ttk.Label(row, text=' %  ').pack(side='left')
        ttk.Button(row, text='保存', width=0, state=state, command=app.change_limit).pack(side='left')
        details = app.section(parent, '容量与设备信息')
        details.pack(fill='x', pady=app.px((0, 12)))
        table = ttk.Frame(details, style='Card.TFrame')
        table.pack(fill='x')
        table.columnconfigure((0, 1), weight=1, uniform='battery_details')
        groups = (
            (('当前能量', 'energy'), ('满充能量', 'full'), ('设计能量', 'design'),
             ('满充电荷量', 'charge_full'), ('设计电荷量', 'charge_design'), ('循环次数', 'cycles')),
            (('当前电压', 'voltage'), ('充放电电流', 'current'), ('制造商', 'manufacturer'),
             ('型号', 'model'), ('电池类型', 'technology')),
        )
        for column, entries in enumerate(groups):
            group = ttk.Frame(table, style='Card.TFrame')
            group.grid(row=0, column=column, sticky='nsew',
                       padx=app.px((0, 12) if column == 0 else (12, 0)))
            group.columnconfigure(1, weight=1)
            for row, (title, key) in enumerate(entries):
                ttk.Label(group, text=title, style='Muted.TLabel').grid(
                    row=row, column=0, sticky='nw', padx=app.px((0, 10)), pady=app.px(5))
                label = ttk.Label(group, textvariable=self.variable(key), justify='left',
                                  wraplength=app.px(135))
                label.grid(row=row, column=1, sticky='nw', pady=app.px(5))
        self.refresh(None)

    def variable(self, key):
        if key not in self.variables:
            self.variables[key] = tk.StringVar(master=self.app.root, value='—')
        return self.variables[key]

    def label(self, parent, key, muted=False, **kwargs):
        if muted:
            kwargs['style'] = 'Muted.TLabel'
        return self.app.wrapped_label(parent, textvariable=self.variable(key), **kwargs)

    def refresh(self, info):
        values = formatted_values(info) if info is not None else {'summary': '正在读取电池信息…'}
        for key, variable in self.variables.items():
            value = values.get(key, '—')
            if self.last_values.get(key) != value:
                variable.set(value)
        self.last_values = values
