"""Regression checks for unnecessary layout and blocking UI work."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from asus_power.gui import configure_changed
from asus_power.power_panel import PowerPanel
from asus_power.theme import rounded_ppm


class PerformanceTests(unittest.TestCase):
    def test_identical_layout_notifications_do_not_reconfigure(self):
        widget = SimpleNamespace(configure=Mock())
        for _ in range(100):
            configure_changed(widget, wraplength=640)
        widget.configure.assert_called_once_with(wraplength=640)
        configure_changed(widget, wraplength=720)
        self.assertEqual(widget.configure.call_count, 2)

    def test_rounded_tiles_are_reused(self):
        rounded_ppm.cache_clear()
        args = (40, 18, '#222222', '#151515')
        first = rounded_ppm(*args)
        self.assertIs(first, rounded_ppm(*args))
        self.assertEqual(rounded_ppm.cache_info().hits, 1)

    def test_power_readback_is_submitted_without_blocking_ui(self):
        panel = PowerPanel.__new__(PowerPanel)
        panel.busy = False
        panel.set_controls = Mock()
        panel.refresh = Mock()
        panel.note = Mock()
        panel.note.get.return_value = '正在应用并检查驱动读数…'
        panel.app = SimpleNamespace(status=Mock(), submit=Mock(),
                                    backend=SimpleNamespace(power_limits=Mock()))
        action = Mock()
        panel.run(action)
        first = panel.app.submit.call_args
        first.kwargs['finished']()
        panel.app.backend.power_limits.assert_not_called()
        readback = panel.app.submit.call_args
        self.assertIs(readback.args[0], panel.app.backend.power_limits)
        self.assertTrue(panel.busy)
        readback.args[1]({'enabled': True})
        readback.kwargs['finished']()
        self.assertFalse(panel.busy)
        panel.refresh.assert_called_once_with({'enabled': True})


import os


@unittest.skipUnless(os.environ.get('ASUS_RUN_GUI_TESTS') == '1', 'opt-in desktop test')
class ScrollTests(unittest.TestCase):
    def tearDown(self):
        import gc
        gc.collect()

    def test_scroll_clips_native_content_and_coalesces_wheel(self):
        import tkinter as tk
        from asus_power.scroll_page import ScrollViewport
        root = tk.Tk()
        theme = SimpleNamespace(bind_colors=lambda widget, **kw: widget.configure(bg='#151515'))
        viewport = ScrollViewport(root, root, theme, lambda value: value)
        try:
            root.geometry('400x200')
            viewport.pack(fill='both', expand=True)
            from tkinter import ttk
            for i in range(30):
                ttk.Label(viewport.content, text=f'测试 {i}').pack()
            root.update()
            self.assertGreater(viewport.content_height, viewport.winfo_height())
            height = viewport.content.winfo_height()
            event = SimpleNamespace(num=5, delta=0)
            for _ in range(3):
                viewport.wheel(event)
            self.assertEqual(viewport.offset, 0)
            self.assertEqual(viewport.pending, 96)
            self.assertEqual(len(root.tk.splitlist(root.tk.call('after', 'info'))), 1)
            viewport.cancel_pending()
            viewport.wheel(event)
            done = tk.BooleanVar(value=False)
            root.after(40, lambda: done.set(True))
            root.wait_variable(done)
            self.assertEqual(viewport.offset, 32)
            self.assertEqual(viewport.content.winfo_y(), -32)
            self.assertEqual(viewport.content.winfo_height(), height)
            viewport.yview_moveto(1)
            root.update()
            self.assertEqual(viewport.yview()[1], 1)
            viewport.yview_moveto(-1)
            self.assertEqual(viewport.offset, 0)
            # Shrinking the content must clamp the offset and remove stale space.
            viewport.yview_moveto(1)
            for child in viewport.content.winfo_children()[2:]:
                child.destroy()
            root.update()
            self.assertEqual(viewport.offset, 0)
            self.assertEqual(viewport.yview()[1], 1)
        finally:
            root.destroy()
