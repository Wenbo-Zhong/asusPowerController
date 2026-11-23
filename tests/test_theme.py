import os
import subprocess
import unittest
from unittest.mock import patch
from asus_power.theme import system_appearance, system_accent, palette, contrast, rgb, GNOME_ACCENTS, PALETTES


class AppearanceTests(unittest.TestCase):
    def runner(self, results):
        def call(args, **kwargs):
            value = next(results)
            if isinstance(value, Exception):
                raise value
            return subprocess.CompletedProcess(args, 0, value, '')
        return call

    def test_portal_preference_takes_priority(self):
        for value, expected in [(1, 'dark'), (2, 'light')]:
            self.assertEqual(system_appearance(self.runner(iter([f'(<<uint32 {value}>>, )']))), expected)

    def test_gnome_fallback_and_theme_name(self):
        self.assertEqual(system_appearance(self.runner(iter(['(<<uint32 0>>,)', "'prefer-dark'"]))), 'dark')
        self.assertEqual(system_appearance(self.runner(iter(['', "'default'", "'Yaru-dark'"]))), 'dark')
        self.assertEqual(system_appearance(self.runner(iter([OSError(), OSError(), OSError()]))), 'light')

    def test_named_gnome_accents_take_priority(self):
        for name, color in GNOME_ACCENTS.items():
            self.assertEqual(system_accent(self.runner(iter([repr(name)]))), color)

    def test_portal_accent_fallback(self):
        self.assertEqual(system_accent(self.runner(iter(['', '(<<(0.5, 0.0, 1.0)>>,)']))), '#8000ff')
        for invalid in ['(<<(-1.0, 0.0, 1.0)>>,)', '(<<(1.1, 0.0, 1.0)>>,)',
                        '(<<(nan, 0.0, 1.0)>>,)', '(<<(1e999, 0.0, 1.0)>>,)', 'garbage']:
            self.assertEqual(system_accent(self.runner(iter(["'unknown'", invalid]))), GNOME_ACCENTS['blue'])
        self.assertEqual(system_accent(self.runner(iter([OSError(), OSError()]))), GNOME_ACCENTS['blue'])

    def test_accent_palette_preserves_neutral_dark_surfaces_and_readable_labels(self):
        for color in [*GNOME_ACCENTS.values(), '#000000', '#ffffff']:
            for mode in ('light', 'dark'):
                colors = palette(mode, color)
                self.assertGreaterEqual(contrast(colors['accent'], colors['button']), 4.5)
                self.assertGreaterEqual(contrast(colors['text'], colors['card']), 4.5)
                if mode == 'dark':
                    for surface in ('bg', 'card', 'field'):
                        r, g, b = rgb(colors[surface])
                        self.assertEqual(r, g)
                        self.assertEqual(g, b)


@unittest.skipUnless(os.environ.get('ASUS_RUN_GUI_TESTS') == '1', 'opt-in desktop rendering test')
class ThemeGuiTests(unittest.TestCase):
    def tearDown(self):
        # Tk interpreters must be collected on the main thread between GUI tests.
        import gc
        gc.collect()

    def test_appearance_changes_without_recreating_widgets(self):
        import tkinter as tk
        from tkinter import ttk
        from asus_power.gui import Application
        root = tk.Tk()
        with patch.object(Application, 'submit'), patch('asus_power.gui.load', return_value={}), \
                patch('asus_power.gui.system_theme', return_value=('light', '#3584e4')) as appearance:
            app = Application(root)
            try:
                # Exercise the normal periodic watcher, not just a direct style call.
                finished = tk.BooleanVar(value=False)
                def transition():
                    self.assertEqual(app.theme.mode, 'light')
                    appearance.return_value = ('dark', '#ed5b00')
                root.after(350, transition)
                # Wait for the observed theme rather than a fixed deadline;
                # compositor load can delay Tk timers and worker polling.
                from time import monotonic
                def wait_theme(mode, accent):
                    deadline = monotonic() + 8
                    finished.set(False)
                    def check():
                        if (app.theme.mode, app.theme.accent) == (mode, accent) or monotonic() >= deadline:
                            finished.set(True)
                        else:
                            root.after(40, check)
                    check()
                    if not finished.get():
                        root.wait_variable(finished)
                wait_theme('dark', '#ed5b00')
                self.assertEqual(app.theme.mode, 'dark')
                self.assertEqual(root.cget('bg'), PALETTES['dark']['bg'])
                self.assertEqual(app.model.cget('fg'), PALETTES['dark']['muted'])
                self.assertEqual(app.theme.accent, '#ed5b00')
                # A color-only system change must repaint even when dark mode stays on.
                appearance.return_value = ('dark', '#9141ac')
                wait_theme('dark', '#9141ac')
                self.assertEqual(app.theme.mode, 'dark')
                self.assertEqual(app.theme.accent, '#9141ac')
                self.assertEqual(app.theme.colors['button'], palette('dark', '#9141ac')['button'])
                # Text/selection values and open dropdown palettes survive live recoloring.
                value = tk.StringVar(value='均衡')
                combo = ttk.Combobox(app.overview, textvariable=value, values=['均衡', '静音'])
                combo.pack()
                popdown = root.tk.call('ttk::combobox::PopdownWindow', combo)
                for mode in ('light', 'dark', 'light'):
                    app.theme.apply(mode)
                    self.assertEqual(value.get(), '均衡')
                    self.assertEqual(root.tk.call(popdown + '.f.l', 'cget', '-background'), PALETTES[mode]['field'])
                    tile = app.theme.images['card']
                    def rgb(color):
                        return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
                    self.assertEqual(tile.get(0, 0), rgb(PALETTES[mode]['bg']))
                    self.assertEqual(tile.get(tile.width() // 2, tile.height() // 2), rgb(PALETTES[mode]['card']))
            finally:
                app.close()
