"""Tk display setup: point fonts and logical pixels share a 96-DPI baseline."""
import math
import os
import re
import subprocess
from tkinter import font


def display_scale(tk_dpi, resources='', override=None):
    """Prefer explicit app scale, then desktop Xft.dpi, then Tk's own DPI."""
    if override is not None:
        try:
            value = float(override)
            if math.isfinite(value) and 0.75 <= value <= 4:
                return value
        except (TypeError, ValueError):
            pass
    match = re.search(r'^Xft\.dpi:\s*([\d.]+)\s*$', resources, re.MULTILINE | re.IGNORECASE)
    try:
        dpi = float(match.group(1)) if match else float(tk_dpi)
    except (ValueError, TypeError):
        dpi = 96
    return max(0.75, min(4, dpi / 96)) if math.isfinite(dpi) else 1


def chinese_family(families):
    """Use families Tk actually exposes, excluding bitmap X11 fallback fonts."""
    available = {name.casefold(): name for name in families}
    for candidate in ('Noto Sans CJK SC', 'Noto Sans SC', 'Source Han Sans SC',
                      'Microsoft YaHei', 'PingFang SC', 'WenQuanYi Micro Hei',
                      'WenQuanYi Zen Hei', 'Droid Sans Fallback'):
        if candidate.casefold() in available:
            return available[candidate.casefold()]
    return None


class Display:
    def __init__(self, root):
        resources = ''
        try:
            resources = subprocess.run(['xrdb', '-query'], capture_output=True,
                                       text=True, timeout=2).stdout
        except (OSError, subprocess.TimeoutExpired):
            pass
        self.scale = display_scale(root.winfo_fpixels('1i'), resources,
                                   os.environ.get('ASUS_POWER_SCALE'))
        root.tk.call('tk', 'scaling', self.scale * 96 / 72)
        self.family = chinese_family(font.families(root))
        if not self.family:
            raise RuntimeError('当前 Tk 无法加载平滑中文字体。请先运行：'
                               'python3 scripts/setup_smooth_tk.py（Ubuntu / Debian）')
        # Tk widgets, ttk, menus, popups and dialogs use consistent named fonts.
        for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont',
                     'TkCaptionFont', 'TkSmallCaptionFont', 'TkIconFont',
                     'TkTooltipFont', 'TkFixedFont'):
            font.nametofont(name, root).configure(family=self.family, size=11)
        root.option_add('*Font', 'TkDefaultFont')
        root.option_add('*TCombobox*Listbox.font', 'TkDefaultFont')

    def px(self, value):
        if isinstance(value, (tuple, list)):
            return tuple(self.px(v) for v in value)
        return round(value * self.scale)

    def font(self, size=11, weight='normal'):
        return self.family, size, weight
