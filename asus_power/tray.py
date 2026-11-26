"""Ayatana status notifier in a separate GTK 3 desktop-session process."""
import ctypes as C

_CALLBACK = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)
_CONNECTION = C.CFUNCTYPE(None, C.c_void_p, C.c_int, C.c_void_p)


class Tray:
    def __init__(self, root, show, quit_app):
        self.root = root
        self.show = show
        self.connected = False
        self.callbacks = []
        gtk = self.gtk = C.CDLL('libgtk-3.so.0')
        indicator = C.CDLL('libayatana-appindicator3.so.1')
        gobject = C.CDLL('libgobject-2.0.so.0')

        def bind(library, name, result, *arguments):
            function = getattr(library, name)
            function.restype = result
            function.argtypes = arguments
            return function

        ptr, string, integer = C.c_void_p, C.c_char_p, C.c_int
        if not bind(gtk, 'gtk_init_check', integer, ptr, ptr)(None, None):
            raise RuntimeError('GTK 无法连接桌面')
        self.pending = bind(gtk, 'gtk_events_pending', integer)
        self.iterate = bind(gtk, 'gtk_main_iteration_do', integer, integer)
        new = bind(indicator, 'app_indicator_new', ptr, string, string, integer)
        self.status = bind(indicator, 'app_indicator_set_status', None, ptr, integer)
        menu = bind(gtk, 'gtk_menu_new', ptr)()
        menu_item = bind(gtk, 'gtk_menu_item_new_with_label', ptr, string)
        append = bind(gtk, 'gtk_menu_shell_append', None, ptr, ptr)
        connect = bind(gobject, 'g_signal_connect_data', C.c_ulong, ptr, string, ptr, ptr, ptr, integer)
        self.item = new(b'asus-power-controller', b'preferences-system-power', 0)
        if not self.item:
            raise RuntimeError('无法创建托盘图标')

        def on_connection(_item, connected, _data):
            was_connected = self.connected
            self.connected = bool(connected)
            if was_connected and not self.connected and root.state() == 'withdrawn':
                root.after_idle(show)

        callback = _CONNECTION(on_connection)
        self.callbacks.append(callback)
        connect(self.item, b'connection-changed', callback, None, None, 0)
        for label, action in [('打开电脑管家', show), ('退出', quit_app)]:
            widget = menu_item(label.encode())
            callback = _CALLBACK(lambda _widget, _data, fn=action: root.after_idle(fn))
            self.callbacks.append(callback)
            connect(widget, b'activate', callback, None, None, 0)
            append(menu, widget)
        bind(gtk, 'gtk_widget_show_all', None, ptr)(menu)
        bind(indicator, 'app_indicator_set_menu', None, ptr, ptr)(self.item, menu)
        self.status(self.item, 1)
        self.timer = root.after(50, self.poll)

    def poll(self):
        for _ in range(20):
            if not self.pending():
                break
            self.iterate(False)
        self.timer = self.root.after(50, self.poll)

    def close(self):
        self.root.after_cancel(self.timer)
        self.status(self.item, 0)


class SessionTray:
    """Keep the indicator on the desktop user's bus when the window is elevated."""

    def __init__(self, root, show, quit_app, uid):
        import os
        from pathlib import Path
        import pwd
        import subprocess
        import sys

        self.root = root
        self.connected = False
        self.buffer = b''
        user = pwd.getpwuid(uid)
        env = dict(os.environ, HOME=user.pw_dir, USER=user.pw_name, LOGNAME=user.pw_name)
        env.pop('PKEXEC_UID', None)
        # The helper uses only the desktop libraries and inherits the authorized
        # process's display environment, but connects as the desktop user.
        credentials = {}
        if os.geteuid() == 0 and user.pw_uid != 0:
            credentials = dict(user=user.pw_uid, group=user.pw_gid,
                               extra_groups=os.getgrouplist(user.pw_name, user.pw_gid))
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env,
            **credentials)

        def receive(stream, _mask):
            data = os.read(stream.fileno(), 4096)
            if not data:
                root.deletefilehandler(stream)
                self.disconnected(show)
                return
            self.buffer += data
            while b'\n' in self.buffer:
                line, self.buffer = self.buffer.split(b'\n', 1)
                if line == b'connected':
                    self.connected = True
                elif line == b'disconnected':
                    self.disconnected(show)
                elif line == b'show':
                    root.after_idle(show)
                elif line == b'quit':
                    root.after_idle(quit_app)

        root.createfilehandler(self.process.stdout, 2, receive)

    def disconnected(self, show):
        was_connected, self.connected = self.connected, False
        if was_connected and self.root.state() == 'withdrawn':
            self.root.after_idle(show)

    def close(self):
        self.root.deletefilehandler(self.process.stdout)
        self.process.stdin.close()
        self.process.stdout.close()
        if self.process.poll() is None:
            self.process.terminate()


def run_helper():
    import os
    import sys

    from desktop_loop import DesktopLoop, GLib
    loop = GLib.MainLoop()
    root = DesktopLoop(lambda: 'normal')

    def send(message):
        print(message, flush=True)

    tray = Tray(root, lambda: send('show'), lambda: send('quit'))
    connected = False

    def report():
        nonlocal connected
        if connected != tray.connected:
            connected = tray.connected
            send('connected' if connected else 'disconnected')
        root.after(100, report)

    def parent_input(file, _mask):
        if not os.read(file.fileno(), 4096):
            tray.close()
            loop.quit()

    root.createfilehandler(sys.stdin, 2, parent_input)
    report()
    loop.run()


if __name__ == '__main__':
    run_helper()
