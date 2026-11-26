"""GTK fan curve table, chart and drag gestures."""
from .drafts import CurveDraft, pwm_percent
from .gtk import Gtk
from .widgets import FAN_NAMES, button, dropdown, flow, label


class CurveEditor:
    def __init__(self, parent):
        self.model = CurveDraft()
        self.keys = []
        self.selected = 0
        self.loading = False
        self.drag = None
        row = flow()
        parent.append(row)
        self.managed = Gtk.CheckButton(label='管理风扇曲线')
        self.switcher = dropdown([])
        self.enabled = Gtk.CheckButton(label='启用此曲线')
        for widget in (self.managed, self.switcher, self.enabled):
            row.insert(widget, -1)
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
        table = flow(maximum=2, homogeneous=True)
        parent.append(table)
        self.inputs = []
        for block in range(2):
            grid = Gtk.Grid(column_spacing=16, row_spacing=6, halign=Gtk.Align.CENTER)
            table.insert(grid, -1)
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
        enabled = bool(self.model.active and self.managed.get_active())
        self.enabled.set_sensitive(enabled)
        for pair in self.inputs:
            for field in pair:
                field.set_sensitive(enabled)

    def load(self, curves, managed):
        self.loading = True
        self.model.load(curves)
        self.keys = list(self.model.curves)
        self.switcher.set_model(Gtk.StringList.new([FAN_NAMES[key] for key in self.keys]))
        key = self.model.active
        self.switcher.set_selected(self.keys.index(key) if key else Gtk.INVALID_LIST_POSITION)
        self.managed.set_active(managed)
        self.show_curve()
        self.loading = False

    def show_curve(self):
        curve = self.model.curves.get(self.model.active)
        self.enabled.set_active(bool(curve and curve['enabled']))
        for index, (temp, pwm) in enumerate(self.inputs):
            temp.set_value(curve['temp'][index] if curve else 0)
            pwm.set_value(pwm_percent(curve['pwm'][index]) if curve else 0)
        self.note.set_label('重合点共用编号标记。可拖动曲线，也可编辑下方数值。' if curve else '请先读取方案曲线。')
        self.sensitivity()
        self.plot.queue_draw()

    def read_points(self):
        temperatures, percentages = [], []
        for temp, pwm in self.inputs:
            temp.update()
            pwm.update()
            temperatures.append(temp.get_value_as_int())
            percentages.append(pwm.get_value())
        self.model.edit(temperatures, percentages, self.enabled.get_active())

    def switch(self):
        index = self.switcher.get_selected()
        if self.loading or index >= len(self.keys):
            return
        self.loading = True
        try:
            if self.model.active and self.managed.get_active():
                self.read_points()
            self.model.select(self.keys[index])
            self.show_curve()
        except ValueError as error:
            self.switcher.set_selected(self.keys.index(self.model.active))
            self.note.set_label(str(error))
        finally:
            self.loading = False

    def export(self):
        managed = self.managed.get_active()
        if managed and self.model.active is not None:
            self.read_points()
        return self.model.export(managed)

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
        if not self.model.active:
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
        if not self.model.active or not self.managed.get_active():
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
