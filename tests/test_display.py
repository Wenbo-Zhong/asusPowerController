import os
import unittest
from unittest.mock import patch
from asus_power.display import display_scale, chinese_family


class DisplayTests(unittest.TestCase):
    def test_desktop_dpi_overrides_x11_96_dpi(self):
        self.assertEqual(display_scale(96, 'Xft.dpi:\t192\n'), 2)
        self.assertEqual(display_scale(96, 'Xft.dpi: 144\n'), 1.5)

    def test_override_is_absolute_and_not_multiplied_twice(self):
        self.assertEqual(display_scale(192, 'Xft.dpi: 192', '1.25'), 1.25)
        self.assertEqual(display_scale(192, ''), 2)

    def test_invalid_scale_falls_back(self):
        for invalid in ('garbage', 'nan', 'inf', '0', '-2', '8'):
            self.assertEqual(display_scale(96, 'Xft.dpi: 192', invalid), 2)
        self.assertEqual(display_scale(float('nan')), 1)
        self.assertEqual(display_scale(96, 'Xft.dpi: invalid'), 1)

    def test_cjk_family_preference_rejects_bitmap_fonts(self):
        self.assertEqual(chinese_family(['Sans', 'unifont', 'Noto Sans CJK SC']), 'Noto Sans CJK SC')
        self.assertIsNone(chinese_family(['fixed', 'unifont', 'unifont sample']))
        self.assertIsNone(chinese_family(['fixed', 'unifont sample']))


@unittest.skipUnless(os.environ.get('ASUS_RUN_GUI_TESTS') == '1', 'opt-in desktop rendering test')
class GuiTests(unittest.TestCase):
    def tearDown(self):
        # Tk interpreters must be collected on the main thread between GUI tests.
        import gc
        gc.collect()

    def test_layout_and_fonts_at_multiple_scales(self):
        import tkinter as tk
        from tkinter import font
        from asus_power.gui import Application
        for scale in ('1', '1.5', '2'):
            with self.subTest(scale=scale), patch.dict(os.environ, ASUS_POWER_SCALE=scale), \
                    patch('asus_power.gui.load', return_value={}), \
                    patch.object(Application, 'submit'):
                import gc
                gc.collect()
                root = tk.Tk()
                try:
                    app = Application(root)
                    # Supply a read-only fixture; never touch actual hardware or policy config.
                    class Backend:
                        def snapshot(self):
                            return {}
                        def capabilities(self):
                            return {'model': 'ProArt 测试设备', 'profiles': ['quiet', 'balanced', 'performance'],
                                    'modern_cli': True, 'charge_limit': True, 'keyboard': True, 'fans': []}
                    self.assertIn('电量 --', app.metrics.get())
                    self.assertEqual(root.globalgetvar(str(app.limit)), '--')
                    self.assertTrue(app.limit_save.instate(['disabled']))
                    app.ready(Backend())
                    root.update()
                    self.assertAlmostEqual(app.display.scale, float(scale))
                    self.assertEqual(font.nametofont('TkTextFont', root).actual('family').casefold(), app.display.family.casefold())
                    self.assertGreater(root.winfo_width(), 700 * float(scale))
                    # Friendly Chinese labels must still save hardware identifiers.
                    from tkinter import ttk
                    def descendants(widget):
                        for child in widget.winfo_children():
                            yield child
                            yield from descendants(child)
                    combos = [w for w in descendants(app.overview) if isinstance(w, ttk.Combobox)]
                    self.assertEqual([v.get() for v in app.policy_labels], ['均衡', '静音'])
                    with patch('asus_power.gui.save') as persist:
                        combos[0].current(2)
                        combos[0].event_generate('<<ComboboxSelected>>')
                        root.update()
                        self.assertEqual(app.ac.get(), 'performance')
                        self.assertEqual(persist.call_args.args[0]['ac'], 'performance')

                    # Force a small viewport and confirm the page scrolls to the last controls.
                    root.geometry(f'{app.px(720)}x{app.px(520)}')
                    root.update()
                    canvas = app.overview.master
                    self.assertGreater(canvas.bbox('all')[3], canvas.winfo_height())
                    canvas.yview_moveto(1)
                    root.update()
                    self.assertAlmostEqual(canvas.yview()[1], 1, places=2)
                    self.assertEqual(len(app.tabs.tabs()), 4)
                    self.assertFalse(hasattr(app, 'battery_panel'))
                    app.tabs.select(app.battery_page.master.master)
                    root.update()
                    self.assertTrue(app.battery_panel.status_card.winfo_viewable())
                finally:
                    app.close()
