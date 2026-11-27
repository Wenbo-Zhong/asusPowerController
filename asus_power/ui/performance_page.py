"""性能工作区：组合实时状态、模式联动和共用草稿的功耗/风扇编辑页。"""
from ..i18n import tr
from .combination_editor import CombinationEditor
from .cooling_page import CoolingPage
from .gtk import Adw, Gtk
from .mode_settings import ModeSettings
from .widgets import box, scroll


class PerformancePage:
    def __init__(self, controller, page):
        self.controller = controller
        self.sections = Gtk.Stack(vexpand=True, hhomogeneous=False)
        page.append(Gtk.StackSwitcher(stack=self.sections, halign=Gtk.Align.CENTER))
        self.editor = CombinationEditor(controller)
        header = box()
        header.add_css_class('dashboard-page')
        header.append(self.editor.header)
        self.draft_header = Adw.Clamp(child=header, maximum_size=840, tightening_threshold=620)
        page.append(self.draft_header)
        page.append(self.sections)
        telemetry = box()
        rules = box()
        self.cooling = CoolingPage(controller, telemetry)
        self.modes = ModeSettings(controller, rules)
        for key, title, content in [('live', tr('performance.live'), telemetry), ('modes', tr('performance.modes'), rules),
                                    ('power', tr('performance.power'), self.editor.power_page),
                                    ('fans', tr('performance.fans'), self.editor.fans_page)]:
            content.add_css_class('dashboard-page')
            self.sections.add_titled(scroll(content), key, title)
        self.sections.connect('notify::visible-child-name', lambda *_: self.update_header())
        self.update_header()

    def update_header(self):
        self.draft_header.set_visible(self.sections.get_visible_child_name() in ('power', 'fans'))

    def show_fans(self):
        self.controller.stack.set_visible_child_name('performance')
        self.sections.set_visible_child_name('fans')

    def ready(self, caps):
        self.cooling.ready(caps)
        self.modes.ready(caps)
        self.editor.populate(caps)

    def refresh(self, state):
        self.cooling.refresh(state)
        self.modes.refresh(state)
        self.editor.refresh(state)
