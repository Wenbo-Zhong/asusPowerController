"""Native curve graph and point editor; percentages are display values only."""
from copy import deepcopy
import math
import tkinter as tk
from tkinter import ttk, messagebox
from .fan_curves import validate

FAN_LABELS = {'cpu': 'CPU 风扇', 'gpu': 'GPU 风扇', 'mid': '第三风扇'}


class FanEditor:
    def __init__(self, app, parent, info, profile_names, draft=False):
        self.app, self.parent = app, parent
        self.draft = draft
        self.px = app.px
        self.profile_names = profile_names
        self.busy = False
        self.dirty = False
        self.loaded = None
        self.curve = None
        self.selected = 0
        self.curves = info.get('curves', {})
        self.controls = []
        self.profile = tk.StringVar(value=profile_names.get(info.get('initial_profile'), ''))
        self.fan = tk.StringVar()
        self.enabled = tk.BooleanVar()
        self.note = tk.StringVar(value=info.get('reason', '拖动曲线上的圆点，或在下方输入温度和风扇输出。'))
        row = ttk.Frame(parent, style='Card.TFrame')
        row.pack(fill='x', pady=self.px((0, 10)))
        self.profile_combo = ttk.Combobox(row, textvariable=self.profile,
            values=[profile_names[p] for p in info.get('profiles', [])], state='readonly', width=10)
        if not draft:
            self.profile_combo.pack(side='left', padx=self.px((0, 10)))
        self.fan_combo = ttk.Combobox(row, textvariable=self.fan, state='readonly', width=12)
        self.fan_combo.pack(side='left')
        self.controls.extend([self.profile_combo, self.fan_combo])
        self.profile_combo.bind('<<ComboboxSelected>>', self.switch_profile)
        self.fan_combo.bind('<<ComboboxSelected>>', self.switch_fan)
        self.canvas = tk.Canvas(parent, height=self.px(220), highlightthickness=0, takefocus=True)
        app.theme.bind_colors(self.canvas, bg='card')
        self.canvas.pack(fill='x')
        self.canvas.bind('<Configure>', lambda e: self.draw())
        self.canvas.bind('<Button-1>', self.select_point)
        self.canvas.bind('<B1-Motion>', self.drag_point)
        app.root.bind('<<AppThemeChanged>>', lambda e: self.draw(), add='+')
        table = ttk.Frame(parent, style='Card.TFrame')
        table.pack(fill='x', padx=self.px(48 if draft else 0), pady=self.px((4, 10)))
        field_gap = self.px((0, 22 if draft else 12))
        row_gap = self.px(5 if draft else 2)
        point_tables = [table]
        if draft:
            table.columnconfigure((0, 2), weight=1, uniform='fan_points')
            table.columnconfigure(1, minsize=self.px(6))
            point_tables = []
            for column, alignment in ((0, 'nw'), (2, 'ne')):
                group = ttk.Frame(table, style='Card.TFrame')
                group.grid(row=0, column=column, sticky=alignment)
                point_tables.append(group)
        for group in point_tables:
            for column, text in enumerate(('点', '温度 °C', '风扇输出 %')):
                ttk.Label(group, text=text, style='Muted.TLabel').grid(
                    row=0, column=column, sticky='w',
                    padx=field_gap if column < 2 else 0)
        self.fields = []
        for index in range(8):
            group = point_tables[index // 4] if draft else table
            row_index = index % 4 + 1 if draft else index + 1
            number = ttk.Label(group, text=str(index + 1))
            number.grid(row=row_index, column=0, sticky='w')
            number.bind('<Button-1>', lambda event, i=index: self.highlight_point(i))
            variables = (tk.StringVar(), tk.StringVar())
            for column, variable in enumerate(variables, start=1):
                field = ttk.Spinbox(group, from_=0, to=100, increment=1, width=6, textvariable=variable,
                                    command=lambda i=index: self.edit_point(i))
                field.grid(row=row_index, column=column, sticky='ew',
                           padx=field_gap if column == 1 else 0, pady=row_gap)
                field.bind('<Return>', lambda e, i=index: self.edit_point(i))
                field.bind('<FocusOut>', lambda e, i=index: self.edit_point(i))
                field.bind('<FocusIn>', lambda e, i=index: self.highlight_point(i))
                self.controls.append(field)
            self.fields.append(variables)
        check = ttk.Checkbutton(parent, text='启用这条自定义曲线', variable=self.enabled, command=self.mark_dirty)
        check.pack(anchor='w')
        self.controls.append(check)
        actions = ttk.Frame(parent, style='Card.TFrame')
        actions.pack(fill='x', pady=self.px((8, 6)))
        actions_list = [('保存到组合草稿', self.apply)] if draft else [('应用曲线', self.apply), ('重新读取', self.reload), ('恢复此方案默认', self.defaults)]
        for text, command in actions_list:
            button = ttk.Button(actions, text=text, width=0, command=command)
            button.pack(side='left', padx=self.px((0, 8)))
            self.controls.append(button)
        app.wrapped_label(parent, textvariable=self.note, style='Muted.TLabel').pack(anchor='w')
        if info.get('supported'):
            self.show_profile(info['initial_profile'], self.curves)
        else:
            self.set_busy(True)

    def set_busy(self, value):
        self.busy = value
        for widget in self.controls:
            state = 'disabled' if value else 'readonly' if isinstance(widget, ttk.Combobox) else 'normal'
            widget.configure(state=state)

    def profile_key(self):
        return next((key for key, label in self.profile_names.items() if label == self.profile.get()), None)

    def fan_key(self):
        return next((key for key, label in FAN_LABELS.items() if label == self.fan.get()), None)

    def discard(self):
        pending = self.dirty
        if self.curve:
            pending = pending or any(
                temp.get() != str(self.curve['temp'][index])
                or pwm.get() != f"{self.curve['pwm'][index] * 100 / 255:.1f}"
                for index, (temp, pwm) in enumerate(self.fields))
        return not pending or messagebox.askokcancel('未应用的曲线', '放弃当前未应用的修改？', parent=self.app.root)

    def finished(self):
        self.set_busy(False)
        if self.note.get().startswith('正在'):
            self.note.set(self.app.status.get())

    def switch_profile(self, event=None):
        if not self.discard():
            self.profile.set(self.profile_names[self.loaded[0]])
            return
        self.load(self.profile_key())

    def switch_fan(self, event=None):
        if not self.discard():
            self.fan.set(FAN_LABELS[self.loaded[1]])
            return
        self.show_curve(self.loaded[0], self.fan_key())

    def load(self, profile):
        self.set_busy(True)
        self.note.set('正在读取曲线…')
        def failed_or_finished():
            self.finished()
            if self.loaded:
                self.profile.set(self.profile_names[self.loaded[0]])
        self.app.submit(lambda: self.app.backend.read_fan_curves(profile),
                        lambda curves: self.show_profile(profile, curves), finished=failed_or_finished)

    def show_profile(self, profile, curves):
        if not curves:
            raise ValueError('当前方案没有可调节的风扇')
        self.curves = curves
        self.profile.set(self.profile_names[profile])
        self.fan_combo.configure(values=[FAN_LABELS[fan] for fan in curves])
        previous = self.loaded[1] if self.loaded else None
        self.show_curve(profile, previous if previous in curves else next(iter(curves)))

    def show_curve(self, profile, fan):
        self.loaded = (profile, fan)
        self.fan.set(FAN_LABELS[fan])
        self.curve = deepcopy(self.curves[fan])
        self.enabled.set(self.curve['enabled'])
        self.dirty = False
        for index in range(8):
            self.sync_fields(index)
        self.note.set('正在编辑组合草稿，纵轴表示风扇输出比例。' if self.draft else '已读取保存的曲线。曲线纵轴表示风扇输出比例，实际转速见下方 RPM 读数。')
        self.draw()

    def sync_fields(self, index):
        self.fields[index][0].set(str(self.curve['temp'][index]))
        self.fields[index][1].set(f"{self.curve['pwm'][index] * 100 / 255:.1f}")

    def mark_dirty(self):
        self.dirty = True
        self.note.set('曲线尚未保存到组合，请保存组合配置。' if self.draft else '有未应用的修改，点击「应用曲线」后生效。')

    def edit_point(self, index):
        if self.busy or self.curve is None:
            return False
        try:
            temperature = int(self.fields[index][0].get())
            percentage = float(self.fields[index][1].get())
            if not math.isfinite(percentage) or not 0 <= percentage <= 100:
                raise ValueError('风扇输出需在 0–100% 内')
            proposed = deepcopy(self.curve)
            proposed['temp'][index] = temperature
            # Preserve raw bytes when the displayed rounded percentage was not edited.
            old = f"{self.curve['pwm'][index] * 100 / 255:.1f}"
            if self.fields[index][1].get() != old:
                proposed['pwm'][index] = round(percentage * 255 / 100)
            validate(proposed)
        except ValueError as error:
            self.note.set(str(error))
            self.sync_fields(index)
            return False
        if proposed != self.curve:
            self.curve = proposed
            self.mark_dirty()
            self.draw()
        return True

    def geometry(self):
        left, top = self.px(45), self.px(20)
        right = max(left + 1, self.canvas.winfo_width() - self.px(20))
        bottom = max(top + 1, self.px(185))
        return left, top, right, bottom

    def point_xy(self, index):
        left, top, right, bottom = self.geometry()
        return (left + self.curve['temp'][index] * (right - left) / 100,
                bottom - self.curve['pwm'][index] * (bottom - top) / 255)

    def draw(self):
        colors = self.app.theme.colors
        signature = (self.geometry(), tuple(colors.items()), self.selected,
                     None if self.curve is None else (tuple(self.curve['temp']), tuple(self.curve['pwm'])))
        if signature == getattr(self, '_draw_signature', None):
            return
        self._draw_signature = signature
        canvas = self.canvas
        canvas.delete('all')
        left, top, right, bottom = self.geometry()
        for value in (0, 25, 50, 75, 100):
            x = left + value * (right - left) / 100
            y = bottom - value * (bottom - top) / 100
            canvas.create_line(left, y, right, y, fill=colors['edge'])
            canvas.create_text(left - self.px(8), y, text=str(value), anchor='e', fill=colors['muted'], font=self.app.display.font(9))
            canvas.create_text(x, bottom + self.px(13), text=str(value), fill=colors['muted'], font=self.app.display.font(9))
        canvas.create_text(right, bottom + self.px(28), text='温度 °C', anchor='e', fill=colors['muted'], font=self.app.display.font(9))
        canvas.create_text(left, top - self.px(12), text='风扇输出 %', anchor='w', fill=colors['muted'], font=self.app.display.font(9))
        if self.curve is None:
            return
        canvas.create_text(right, top - self.px(12),
            text=f"第 {self.selected + 1} 点 · {self.curve['temp'][self.selected]}°C / {self.curve['pwm'][self.selected] * 100 / 255:.1f}%",
            anchor='e', fill=colors['text'], font=self.app.display.font(9))
        points = [self.point_xy(index) for index in range(8)]
        canvas.create_line(*[coordinate for point in points for coordinate in point], fill=colors['accent'], width=self.px(2))
        radius = self.px(5)
        for (x, y), indices in self.point_groups().items():
            canvas.create_oval(x-radius, y-radius, x+radius, y+radius,
                               fill=colors['accent'] if self.selected in indices else colors['card'],
                               outline=colors['accent'], width=self.px(2))
            numbers = '/'.join(str(index + 1) for index in indices)
            # Keep the label inside the plot at its upper and right edges.
            canvas.create_text(
                x - self.px(8) if x > right - self.px(45) else x + self.px(8),
                y + self.px(8) if y < top + self.px(12) else y - self.px(8),
                text=numbers, anchor='e' if x > right - self.px(45) else 'w',
                fill=colors['text'], font=self.app.display.font(9), tags=('point-number',))

    def point_groups(self):
        groups = {}
        for index in range(8):
            groups.setdefault(self.point_xy(index), []).append(index)
        return groups

    def highlight_point(self, index):
        if not self.busy and self.curve is not None:
            self.selected = index
            self.draw()

    def select_point(self, event):
        if self.busy or self.curve is None:
            return
        groups = self.point_groups()
        def distance(point):
            x, y = point
            return math.hypot(event.x - x, event.y - y)

        point = min(groups, key=distance)
        indices = groups[point]
        self.dragging = distance(point) <= self.px(16)
        if self.dragging and self.selected in indices:
            self.selected = indices[(indices.index(self.selected) + 1) % len(indices)]
        else:
            self.selected = indices[0]
        self.canvas.focus_set()
        self.draw()

    def drag_point(self, event):
        if self.busy or not getattr(self, 'dragging', False):
            return
        index = self.selected
        left, top, right, bottom = self.geometry()
        lower_t = self.curve['temp'][index-1] if index else 0
        upper_t = self.curve['temp'][index+1] if index < 7 else 100
        lower_p = self.curve['pwm'][index-1] if index else 0
        upper_p = self.curve['pwm'][index+1] if index < 7 else 255
        self.curve['temp'][index] = max(lower_t, min(upper_t, round((event.x-left)*100/(right-left))))
        self.curve['pwm'][index] = max(lower_p, min(upper_p, round((bottom-event.y)*255/(bottom-top))))
        self.sync_fields(index)
        self.mark_dirty()
        self.draw()

    def reload(self):
        if self.discard():
            self.load(self.loaded[0])

    def apply(self):
        if self.busy or self.curve is None:
            return
        if not all(self.edit_point(index) for index in range(8)):
            return
        profile = self.loaded[0]
        curve = deepcopy(self.curve)
        curve['enabled'] = self.enabled.get()
        validate(curve)
        if self.draft:
            self.curves[curve['fan']] = curve
            self.show_profile(profile, self.curves)
            self.note.set('已保存到组合草稿；点击页面上方「保存全部配置」后保留。')
            return
        self.set_busy(True)
        self.note.set('正在保存并回读曲线…')
        self.app.submit(lambda: self.app.backend.save_fan_curve(profile, curve),
                        lambda curves: self.show_profile(profile, curves),
                        finished=self.finished)

    def export_draft(self):
        if not self.draft or self.busy or self.curve is None:
            raise ValueError('请先读取此方案的风扇曲线')
        if not all(self.edit_point(index) for index in range(8)):
            raise ValueError(self.note.get())
        curve = deepcopy(self.curve)
        curve['enabled'] = self.enabled.get()
        validate(curve)
        curves = deepcopy(self.curves)
        curves[curve['fan']] = curve
        # Commit to the draft model as well: switching to another fan after a
        # page save must not reload stale points and overwrite the saved fan.
        self.curves = deepcopy(curves)
        self.curve = curve
        self.dirty = False
        for index in range(8):
            self.sync_fields(index)
        return deepcopy(curves)

    def defaults(self):
        if self.busy or self.loaded is None:
            return
        profile = self.loaded[0]
        if not messagebox.askokcancel('恢复方案默认曲线',
            f'恢复「{self.profile_names[profile]}」方案所有风扇的原厂曲线？当前修改会被替换。', parent=self.app.root):
            return
        self.set_busy(True)
        self.note.set('正在恢复默认曲线…')
        self.app.submit(lambda: self.app.backend.reset_fan_curves(profile),
                        lambda curves: self.show_profile(profile, curves),
                        finished=self.finished)
