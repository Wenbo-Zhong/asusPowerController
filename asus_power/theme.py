"""Rounded ttk elements and Linux appearance discovery; standard library only."""
from functools import lru_cache
import math
import re
import subprocess
import tkinter as tk
from tkinter import ttk

# GNOME/libadwaita accent values (no GTK dependency):
# https://gnome.pages.gitlab.gnome.org/libadwaita/doc/1.6/css-variables.html
GNOME_ACCENTS = {
    'blue': '#3584e4', 'teal': '#2190a4', 'green': '#3a944a',
    'yellow': '#c88800', 'orange': '#ed5b00', 'red': '#e62d42',
    'pink': '#d56199', 'purple': '#9141ac', 'slate': '#6f8396',
}
ACCENT_TEXT = {
    '#3584e4': ('#0461be', '#81d0ff'), '#2190a4': ('#007184', '#7bdff4'),
    '#3a944a': ('#15772e', '#8de698'), '#c88800': ('#905300', '#ffc057'),
    '#ed5b00': ('#b62200', '#ff9c5b'), '#e62d42': ('#c00023', '#ff888c'),
    '#d56199': ('#a2326c', '#ffa0d8'), '#9141ac': ('#8939a4', '#fba7ff'),
    '#6f8396': ('#526678', '#bbd1e5'),
}
PALETTES = {
    'light': dict(bg='#f4f3ef', card='#fffefa', text='#343434', muted='#707070',
                  field='#f3f3f0', edge='#d2d2ce', disabled='#ecece8', thumb='#c0c0bc'),
    'dark': dict(bg='#151515', card='#222222', text='#e6e6e6', muted='#ababab',
                 field='#1b1b1b', edge='#424242', disabled='#2b2b2b', thumb='#555555'),
}


def rgb(color):
    return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))


def blend(color, background, amount):
    return '#' + ''.join(f'{round(a * amount + b * (1 - amount)):02x}'
                         for a, b in zip(rgb(color), rgb(background)))


def contrast(a, b):
    def luminance(color):
        channels = [v / 255 for v in rgb(color)]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
        return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    low, high = sorted((luminance(a), luminance(b)))
    return (high + 0.05) / (low + 0.05)


def palette(mode, accent):
    result = dict(PALETTES[mode])
    foreground = ACCENT_TEXT.get(accent, (accent, accent))[mode == 'dark']
    # Custom portal colors also need readable text on selected tabs.
    for step in range(21):
        if contrast(foreground, result['card']) >= 4.5:
            break
        foreground = blend('#ffffff' if mode == 'dark' else '#000000', accent, step / 20)
    result.update(accent=foreground,
                  button=blend(accent, result['card'], 0.18 if mode == 'dark' else 0.12),
                  hover=blend(accent, result['card'], 0.28 if mode == 'dark' else 0.21))
    return result


def query_setting(args, runner):
    try:
        result = runner(args, capture_output=True, text=True, timeout=2)
        return result.stdout.strip() if result.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


def system_accent(runner=subprocess.run):
    """Prefer the GNOME accent setting, then a validated portal sRGB triplet."""
    name = query_setting(['gsettings', 'get', 'org.gnome.desktop.interface', 'accent-color'], runner).strip("'\"")
    if name in GNOME_ACCENTS:
        return GNOME_ACCENTS[name]
    value = query_setting(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.portal.Desktop',
                           '--object-path', '/org/freedesktop/portal/desktop', '--method',
                           'org.freedesktop.portal.Settings.Read', 'org.freedesktop.appearance', 'accent-color'], runner)
    number = r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)'
    match = re.search(r'\(\s*' + r'\s*,\s*'.join([number] * 3) + r'\s*\)', value)
    if match:
        channels = tuple(float(v) for v in match.groups())
        if all(math.isfinite(v) and 0 <= v <= 1 for v in channels):
            return '#' + ''.join(f'{round(v * 255):02x}' for v in channels)
    return GNOME_ACCENTS['blue']


def system_theme(runner=subprocess.run):
    return system_appearance(runner), system_accent(runner)


def system_appearance(runner=subprocess.run):
    """Desktop portal (GNOME/KDE), then GNOME settings. Never writes settings."""
    def query(args):
        return query_setting(args, runner)
    value = query(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.portal.Desktop',
                   '--object-path', '/org/freedesktop/portal/desktop', '--method',
                   'org.freedesktop.portal.Settings.Read', 'org.freedesktop.appearance', 'color-scheme'])
    match = re.search(r'uint32\s+(\d+)', value)
    if match and match[1] in ('1', '2'):
        return 'dark' if match[1] == '1' else 'light'
    value = query(['gsettings', 'get', 'org.gnome.desktop.interface', 'color-scheme'])
    if 'prefer-dark' in value:
        return 'dark'
    if 'prefer-light' in value:
        return 'light'
    value = query(['gsettings', 'get', 'org.gnome.desktop.interface', 'gtk-theme'])
    return 'dark' if 'dark' in value.lower() else 'light'


@lru_cache(maxsize=128)
def rounded_ppm(size, radius, fill, outside, edge=None, stroke=1):
    """Antialiased round-rect tile for ttk nine-slice scaling, without Pillow."""
    def rgb(color):
        return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
    fg, bg, border = rgb(fill), rgb(outside), rgb(edge or fill)
    result = bytearray()
    half = size / 2
    # Signed distance to a rounded box, one-pixel coverage ramp at the boundary.
    for y in range(size):
        for x in range(size):
            dx = abs(x + 0.5 - half) - (half - radius)
            dy = abs(y + 0.5 - half) - (half - radius)
            distance = math.hypot(max(dx, 0), max(dy, 0)) + min(max(dx, dy), 0) - radius
            alpha = min(1, max(0, 0.5 - distance))
            inner = min(1, max(0, 0.5 - distance - stroke)) if edge else 1
            color = [fg[c] * inner + border[c] * (1 - inner) for c in range(3)]
            result.extend(round(color[c] * alpha + bg[c] * (1 - alpha)) for c in range(3))
    return f'P6\n{size} {size}\n255\n'.encode() + bytes(result)


@lru_cache(maxsize=64)
def checkbox_ppm(size, radius, fill, outside, edge, mark):
    data = rounded_ppm(size, radius, fill, outside, edge, max(1, size / 20))
    if mark is None:
        return data
    header = f'P6\n{size} {size}\n255\n'.encode()
    pixels = bytearray(data[len(header):])
    points = [(size * x, size * y) for x, y in ((.26, .51), (.43, .68), (.75, .34))]
    foreground = rgb(mark)
    for y in range(size):
        for x in range(size):
            distances = []
            for (ax, ay), (bx, by) in zip(points, points[1:]):
                dx, dy = bx - ax, by - ay
                fraction = ((x + .5 - ax) * dx + (y + .5 - ay) * dy) / (dx * dx + dy * dy)
                fraction = max(0, min(1, fraction))
                distances.append(math.hypot(x + .5 - ax - fraction * dx, y + .5 - ay - fraction * dy))
            coverage = max(0, min(1, size * .055 + .5 - min(distances)))
            offset = (y * size + x) * 3
            for channel in range(3):
                current = pixels[offset + channel]
                pixels[offset + channel] = round(current + (foreground[channel] - current) * coverage)
    return header + bytes(pixels)


class RoundedTheme:
    def __init__(self, root, display):
        self.root, self.display = root, display
        self.style = ttk.Style(root)
        self.style.theme_use('clam')
        self.images = {}
        self.bindings = []
        self.mode = 'light'
        self.accent = GNOME_ACCENTS['blue']
        self.colors = palette(self.mode, self.accent)
        self._make_elements()
        self.apply(self.mode, _refresh_images=False)

    def tile(self, name, radius, fill, outside, edge=None):
        radius = max(2, self.display.px(radius))
        # ttk tiles the middle of an image; it does not stretch it. A 4px
        # centre in a large card creates tens of thousands of X11 image copies.
        # Keep small control assets compact so their minimum size stays unchanged.
        size = radius * 2 + (self.display.px(32) if name == 'card' else 4)
        data = rounded_ppm(size, radius, fill, outside, edge, self.display.px(1))
        self.set_image(name, data)
        return self.images[name]

    def set_image(self, name, data):
        if name in self.images:
            self.images[name].configure(data=data, format='PPM')
        else:
            self.images[name] = tk.PhotoImage(master=self.root, data=data, format='PPM')

    def _tiles(self):
        p = self.colors
        for name, radius, fill, outside, edge in (
            ('card', 18, p['card'], p['bg'], None),
            ('button', 12, p['button'], p['card'], None),
            ('hover', 12, p['hover'], p['card'], None),
            ('focus', 12, p['button'], p['card'], p['accent']),
            ('disabled', 12, p['disabled'], p['card'], None),
            ('field', 10, p['field'], p['card'], p['edge']),
            ('field-focus', 10, p['field'], p['card'], p['accent']),
            ('tab', 12, p['bg'], p['bg'], None),
            ('tab-selected', 12, p['button'], p['bg'], None),
            ('tab-hover', 12, p['hover'], p['bg'], None),
            ('thumb', 4, p['thumb'], p['bg'], None),
        ):
            self.tile(name, radius, fill, outside, edge)
        px = self.display.px
        for name, fill, edge, mark in (
            ('check', p['field'], p['muted'], None),
            ('check-focus', p['field'], p['accent'], None),
            ('check-selected', p['accent'], None, p['card']),
            ('check-disabled', p['disabled'], p['edge'], None),
            ('check-disabled-selected', p['disabled'], p['edge'], p['muted']),
        ):
            data = checkbox_ppm(px(20), px(5), fill, p['card'], edge, mark)
            self.set_image(name, data)


    def _make_elements(self):
        self._tiles()
        s = self.style
        def element(name, base, radius, states=()):
            s.element_create(name, 'image', self.images[base],
                             *[(state, self.images[image]) for state, image in states],
                             border=self.display.px(radius), padding=0, sticky='nsew')
        s.element_create('Soft.check', 'image', self.images['check'],
                         ('disabled selected', self.images['check-disabled-selected']),
                         ('disabled', self.images['check-disabled']),
                         ('selected', self.images['check-selected']),
                         ('focus', self.images['check-focus']), sticky='')
        s.layout('TCheckbutton', [('Checkbutton.padding', {'sticky': 'nsew', 'children': [
            ('Soft.check', {'side': 'left', 'sticky': ''}),
            ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
                ('Checkbutton.label', {'sticky': 'w'})]})]})])
        element('Soft.card', 'card', 18)
        element('Soft.button', 'button', 12, [('disabled', 'disabled'), ('pressed', 'hover'),
                                             ('focus', 'focus'), ('active', 'hover')])
        element('Soft.field', 'field', 10, [('focus', 'field-focus')])
        element('Soft.tab', 'tab', 12, [('selected', 'tab-selected'), ('active', 'tab-hover')])
        element('Soft.thumb', 'thumb', 4)
        s.layout('Rounded.TFrame', [('Soft.card', {'sticky': 'nsew'})])
        s.layout('TButton', [('Soft.button', {'sticky': 'nsew', 'children': [
            ('Button.padding', {'sticky': 'nsew', 'children': [('Button.label', {'sticky': 'nsew'})]})]})])
        s.layout('TNotebook.Tab', [('Soft.tab', {'sticky': 'nsew', 'children': [
            ('Notebook.padding', {'sticky': 'nsew', 'children': [
                ('Notebook.label', {'sticky': 'nsew'})]})]})])
        s.element_create('Soft.sectionTab', 'image', self.images['tab'],
                         ('selected', self.images['tab-selected']),
                         ('active', self.images['tab-hover']),
                         border=self.display.px(12), padding=self.display.px((16, 7)))
        s.layout('Sections.TNotebook.Tab', [('Notebook.padding', {'sticky': 'nsew', 'children': [
            ('Soft.sectionTab', {'sticky': 'nsew', 'children': [
                ('Notebook.label', {'sticky': 'nsew'})]})]})])
        # Eliminate the rectangular notebook client outline.
        s.layout('TNotebook', [])
        for name, prefix in [('TEntry', 'Entry'), ('TSpinbox', 'Spinbox'), ('TCombobox', 'Combobox')]:
            children = []
            if name == 'TCombobox':
                children.append(('Combobox.downarrow', {'side': 'right', 'sticky': ''}))
            if name == 'TSpinbox':
                children.append(('null', {'side': 'right', 'children': [
                    ('Spinbox.uparrow', {'side': 'top'}), ('Spinbox.downarrow', {'side': 'bottom'})]}))
            children.append((prefix + '.textarea', {'sticky': 'nsew'}))
            s.layout(name, [('Soft.field', {'sticky': 'nsew', 'children': [
                (prefix + '.padding', {'sticky': 'nsew', 'children': children})]})])
        s.layout('Vertical.TScrollbar', [('Vertical.Scrollbar.trough', {'sticky': 'ns', 'children': [
            ('Soft.thumb', {'sticky': 'nsew', 'expand': 1})]})])

    def bind_colors(self, widget, **options):
        """Register classic Tk widget colors; ttk uses shared styles."""
        self.bindings.append((widget, options))
        widget.configure(**{option: self.colors[key] for option, key in options.items()})

    def apply(self, mode, accent=None, *, _refresh_images=True):
        self.mode = mode
        if accent is not None:
            self.accent = accent
        self.colors = palette(mode, self.accent)
        if _refresh_images:
            self._tiles()
        p, s, px = self.colors, self.style, self.display.px
        s.configure('.', background=p['card'], foreground=p['text'], font=self.display.font())
        for name, color in [('TFrame', 'bg'), ('Card.TFrame', 'card'), ('Rounded.TFrame', 'card'),
                            ('TLabel', 'card'), ('TNotebook', 'bg')]:
            s.configure(name, background=p[color], borderwidth=0)
        s.configure('Muted.TLabel', foreground=p['muted'])
        s.configure('TButton', padding=px((16, 9)), borderwidth=0)
        s.map('TButton', foreground=[('disabled', p['muted']), ('!disabled', p['text'])])
        s.configure('TNotebook.Tab', padding=px((20, 11)))
        s.map('TNotebook.Tab', padding=[('selected', px((20, 11))), ('!selected', px((20, 11)))], foreground=[('selected', p['accent']), ('!selected', p['muted'])])
        gap = px((4, 0))
        s.configure('Sections.TNotebook.Tab', padding=gap)
        s.map('Sections.TNotebook.Tab', padding=[('selected', gap), ('!selected', gap)])
        for name in ('TEntry', 'TSpinbox', 'TCombobox'):
            s.configure(name, fieldbackground=p['field'], background=p['field'], foreground=p['text'],
                        insertcolor=p['text'], padding=px((10, 8)), arrowsize=px(12),
                        arrowcolor=p['muted'], borderwidth=0, bordercolor=p['field'],
                        lightcolor=p['field'], darkcolor=p['field'])
            s.map(name, fieldbackground=[('readonly', p['field'])],
                  foreground=[('disabled', p['muted']), ('!disabled', p['text'])],
                  selectbackground=[('!disabled', p['hover'])], selectforeground=[('!disabled', p['text'])])
        s.configure('TCheckbutton', background=p['card'], foreground=p['text'],
                    padding=px((0, 3)), focuscolor=p['accent'])
        s.map('TCheckbutton', background=[('active', p['card'])],
              foreground=[('disabled', p['muted']), ('!disabled', p['text'])])
        s.configure('Vertical.TScrollbar', width=px(8), troughcolor=p['bg'], background=p['thumb'],
                    bordercolor=p['bg'], lightcolor=p['bg'], darkcolor=p['bg'], borderwidth=0)
        self.root.option_add('*TCombobox*Listbox.background', p['field'])
        self.root.option_add('*TCombobox*Listbox.foreground', p['text'])
        self.root.option_add('*TCombobox*Listbox.selectBackground', p['hover'])
        self.root.option_add('*TCombobox*Listbox.selectForeground', p['text'])
        # Existing popdown lists do not reread option database values.
        def visit(widget):
            if isinstance(widget, ttk.Combobox):
                popdown = str(widget) + '.popdown'
                if not self.root.tk.call('winfo', 'exists', popdown + '.f.l'):
                    return
                self.root.tk.call(popdown + '.f.l', 'configure', '-background', p['field'],
                                  '-foreground', p['text'], '-selectbackground', p['hover'],
                                  '-selectforeground', p['text'])
            for child in widget.winfo_children():
                visit(child)
        visit(self.root)
        for widget, options in self.bindings:
            if widget.winfo_exists():
                widget.configure(**{option: p[key] for option, key in options.items()})
        self.root.event_generate('<<AppThemeChanged>>', when='tail')
