"""A clipped native widget viewport, avoiding Canvas window-item repainting."""
import tkinter as tk
from tkinter import ttk


class ScrollViewport(tk.Frame):
    def __init__(self, parent, root, theme, px):
        super().__init__(parent, borderwidth=0, highlightthickness=0)
        self.root = root
        self.px = px
        self.offset = 0
        self.pending = 0
        self.timer = None
        self.content_height = 1
        self.scrollcommand = None
        theme.bind_colors(self, bg='bg')
        self.content = ttk.Frame(self, padding=px((0, 0, 0, 12)))
        self.content.place(x=0, y=0, relwidth=1)
        self.bind('<Configure>', self.layout)
        self.content.bind('<Configure>', self.layout)
        self.bind('<Destroy>', self.destroyed, add='+')

    def destroyed(self, event):
        if event.widget is self and self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None

    def layout(self, event=None):
        self.content_height = max(1, self.content.winfo_reqheight())
        self.move(self.offset)

    def move(self, offset):
        offset = max(0, min(round(offset), max(0, self.content_height - self.winfo_height())))
        if offset != self.offset:
            self.offset = offset
            self.content.place_configure(y=-offset)
        if self.scrollcommand:
            self.scrollcommand(*self.yview())

    def bbox(self, tag=None):
        return (0, 0, self.winfo_width(), self.content_height)

    def yview(self, *args):
        if args:
            if args[0] == 'moveto':
                self.yview_moveto(float(args[1]))
            elif args[0] == 'scroll':
                self.yview_scroll(int(args[1]), args[2])
            return
        return (self.offset / self.content_height,
                min(1., (self.offset + self.winfo_height()) / self.content_height))

    def yview_moveto(self, fraction):
        self.cancel_pending()
        self.move(fraction * self.content_height)

    def yview_scroll(self, number, what):
        self.cancel_pending()
        step = self.px(16) if what == 'units' else max(1, self.winfo_height() * .9)
        self.move(self.offset + number * step)

    def cancel_pending(self):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        self.pending = 0

    def wheel(self, event):
        if self.content_height <= self.winfo_height():
            return
        number = getattr(event, 'num', None)
        delta = getattr(event, 'delta', 0)
        if number == 4:
            step = -1
        elif number == 5:
            step = 1
        elif delta:
            step = -delta / 120
        else:
            return
        self.pending += step * self.px(32)
        if self.timer is None:
            self.timer = self.after(16, self.flush_wheel)

    def flush_wheel(self):
        self.timer = None
        distance, self.pending = self.pending, 0
        # Coalesce high-frequency input into a single native window move.
        self.move(self.offset + distance)
