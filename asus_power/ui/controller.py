"""主窗口生命周期、后台采样与自动策略；连接各页面、配置及 backend。"""
from ..i18n import tr
from concurrent.futures import ThreadPoolExecutor
import os

from ..backend import LinuxBackend
from ..combinations import CombinationPolicy, normalized
from ..config import Policy, load, save
from ..desktop_loop import DesktopLoop, once
from ..instance import Instance
from ..keyboard_idle import KeyboardIdle
from ..tray import SessionTray
from .battery_page import BatteryPage
from .gtk import Adw, Gdk, Gio, GLib, Gtk
from .overview_page import OverviewPage
from .performance_page import PerformancePage
from .settings_page import LIGHT_TIMEOUTS, SettingsPage
from .widgets import box, label, scroll


class Controller:
    def __init__(self, app, instance, start_hidden):
        self.app = app
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.firmware_pool = ThreadPoolExecutor(max_workers=1)
        self.keyboard_idle = None
        self.keyboard_busy = False
        self.backend = None
        self.caps = {}
        self.state = {}
        self.mode_keys = []
        self.policy = Policy()
        self.combination_policy = CombinationPolicy()
        self.settings = load()
        self.closing = False
        self.tray = None
        self.window = Adw.ApplicationWindow(application=app, title=tr('app.name'), default_width=900,
                                            default_height=740, icon_name='preferences-system-power')
        self.loop = DesktopLoop(lambda: 'normal' if self.window.get_visible() else 'withdrawn')
        self.window.connect('close-request', self.close_window)
        instance.attach(self.loop, self.window.present, self.quit)
        preset_error = None
        try:
            self.combinations = normalized(self.settings.get('combinations'))
        except ValueError as error:
            self.combinations = normalized(None)
            preset_error = str(error)

        css = Gtk.CssProvider()
        css.load_from_data(b"""
            .dashboard-card { background: @card_bg_color; border-radius: 16px; padding: 18px; }
            .dashboard-heading { font-weight: 700; font-size: 1.08em; }
            .dashboard-page { padding: 12px 24px 18px; }
            .dashboard-nav { padding: 14px 24px 4px; }
            .dashboard-nav button { border-radius: 12px; padding: 5px 15px; }
            .dashboard-metric { font-weight: 700; font-size: 1.3em; }
            .dashboard-value { font-feature-settings: "tnum"; }
            .dashboard-status { padding: 6px 24px 12px; }
            .dashboard-flow > flowboxchild { padding: 0; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.stack = Gtk.Stack(vexpand=True, hhomogeneous=False)
        header.set_title_widget(Adw.WindowTitle(title=tr('app.name')))
        toolbar.add_top_bar(header)
        content = box(spacing=0)
        toolbar.set_content(content)
        self.window.set_content(toolbar)
        navigation = box(True)
        navigation.add_css_class('dashboard-nav')
        navigation.append(Gtk.StackSwitcher(stack=self.stack, halign=Gtk.Align.START))
        content.append(navigation)
        content.append(self.stack)
        self.status = label(tr('app.reading'), subdued=True)
        self.status.add_css_class('dashboard-status')
        content.append(self.status)
        pages = {}
        for name, title in [('overview', tr('page.overview')), ('performance', tr('page.performance')),
                            ('battery', tr('page.battery')), ('settings', tr('page.settings'))]:
            page = box()
            pages[name] = page
            if name != 'performance':
                page.add_css_class('dashboard-page')
            self.stack.add_titled(page if name == 'performance' else scroll(page), name, title)
        self.overview = OverviewPage(self, pages['overview'])
        self.performance = PerformancePage(self, pages['performance'])
        self.battery = BatteryPage(self, pages['battery'])
        self.settings_page = SettingsPage(self, pages['settings'])
        if preset_error:
            self.performance.editor.note.set_label(tr('app.old_combination', error=preset_error))
        if not start_hidden:
            self.window.present()
        self.submit(self.detect, self.ready)
        GLib.idle_add(once(lambda: self.start_tray(start_hidden)))
        self.loop.after(1000, self.tick_keyboard)

    def start_tray(self, hidden):
        try:
            uid = int(os.environ.get('PKEXEC_UID', os.getuid())) if os.geteuid() == 0 else os.getuid()
            self.tray = SessionTray(self.loop, self.window.present, self.quit, uid)
        except (OSError, ValueError):
            pass
        if hidden:
            self.loop.after(2500 if self.tray else 0, self.show_without_tray)

    def show_without_tray(self):
        if not self.closing and (not self.tray or not self.tray.connected):
            self.window.present()

    def close_window(self, *_):
        if self.tray and self.tray.connected:
            self.window.set_visible(False)
        else:
            self.quit()
        return True

    def quit(self):
        if self.closing:
            return
        self.closing = True
        if self.tray:
            self.tray.close()
        self.window.destroy()
        self.app.quit()

    def submit(self, operation, done=None, finished=None, executor=None, error=None):
        # 硬件/磁盘操作在线程池执行；所有控件更新和完成回调回到 GLib 主线程。
        # 关闭窗口后丢弃排队的界面回调，避免访问已销毁的 GTK 控件。
        future = (executor or self.pool).submit(operation)

        def receive():
            if self.closing:
                return
            try:
                result = future.result()
                if done:
                    done(result)
                elif isinstance(result, str):
                    self.status.set_label(result)
            except Exception as failure:
                message = tr('app.failed', failure=failure)
                self.status.set_label(message)
                if error is not None and error is not self.status:
                    error.set_label(message)
            finally:
                if finished:
                    finished()

        future.add_done_callback(lambda _: GLib.idle_add(once(receive)))

    def perform(self, operation, *, control=None, notice=None, pending=None,
                done=None, finished=None, executor=None):
        if control is not None:
            control.set_sensitive(False)
        target = notice if notice is not None else self.status
        if pending:
            target.set_label(pending)

        def complete():
            if control is not None:
                control.set_sensitive(True)
            if finished:
                finished()

        self.submit(operation, done if done is not None else target.set_label,
                    complete, executor=executor, error=target)

    @staticmethod
    def detect():
        backend = LinuxBackend()
        return backend, backend.capabilities()

    def persist(self, **values):
        proposed = dict(self.settings, **values)
        try:
            save(proposed)
        except OSError as error:
            self.status.set_label(tr('app.save_failed', error=error))
            return False
        self.settings = proposed
        return True

    def ready(self, result):
        self.backend, self.caps = result
        self.mode_keys = self.caps['profiles']
        for page in (self.overview, self.performance, self.battery, self.settings_page):
            page.ready(self.caps)
        self.status.set_label(tr('app.ready'))
        self.tick()

    def tick(self):
        if not self.closing:
            self.submit(self.backend.snapshot, self.update,
                        finished=lambda: self.loop.after(3000, self.tick))

    def update(self, state):
        self.state = state
        for page in (self.overview, self.performance, self.battery):
            page.refresh(state)
        ac, profile = state['ac'], state['profile']
        mode = self.policy.target(ac, self.settings)
        if mode and mode != profile and mode in self.mode_keys and self.backend.modern:
            self.submit(lambda: self.backend.switch_profile_on_power_change(mode, profile, ac))
            return
        request = self.combination_policy.request(state, self.combinations)
        if request and (request[2]['power'] or request[2]['fans']):
            revision = self.combination_policy.revision
            # 入队不等于已执行；开始写硬件前必须再次验证保存版本与上下文。
            self.submit(lambda: self.combination_policy.run_pending(
                request, revision, self.combinations, self.backend.apply_combination))

    def tick_keyboard(self):
        if self.closing:
            return
        self.loop.after(1000, self.tick_keyboard)
        ac = self.state.get('ac')
        source = 'ac' if ac else 'battery'
        settings = self.settings.get('keyboard_timeout')
        seconds = settings.get(source, 0) if ac is not None and isinstance(settings, dict) else 0
        if seconds not in LIGHT_TIMEOUTS:
            seconds = 0
        restore_pending = self.keyboard_idle and self.keyboard_idle.restore_level is not None
        if self.keyboard_busy or (not seconds and not restore_pending):
            return
        self.keyboard_busy = True

        def update():
            try:
                if self.keyboard_idle is None:
                    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                    self.keyboard_idle = KeyboardIdle(connection, Gio, GLib)
                self.keyboard_idle.update(seconds)
            except GLib.Error:
                return tr('keyboard.session_unavailable')
            return tr('keyboard.activity_note')

        self.submit(update, self.settings_page.keyboard_note.set_label,
                    finished=lambda: setattr(self, 'keyboard_busy', False))


def launch(start_hidden=False):
    GLib.set_prgname('asus-power-controller')
    GLib.set_application_name(tr('app.name'))
    instance = Instance(start_hidden)
    if not instance.acquire():
        return
    app = Adw.Application(flags=Gio.ApplicationFlags.NON_UNIQUE)
    controller = None

    def activate(application):
        nonlocal controller
        if controller is None:
            controller = Controller(application, instance, start_hidden)
        else:
            controller.window.present()

    app.connect('activate', activate)
    try:
        app.run([])
    finally:
        if controller:
            controller.pool.shutdown(wait=True, cancel_futures=True)
            if controller.keyboard_idle:
                try:
                    controller.keyboard_idle.restore()
                except GLib.Error:
                    pass
            controller.firmware_pool.shutdown(wait=False, cancel_futures=True)
        instance.close()
