"""为托盘与单实例通信适配 GLib 事件源；一次性回调通过 once 移除事件源。"""
from gi.repository import GLib


def once(callback):
    def dispatch():
        callback()
        return GLib.SOURCE_REMOVE
    return dispatch


class DesktopLoop:
    def __init__(self, state):
        self.state = state
        self.watches = {}

    def after(self, milliseconds, callback):
        return GLib.timeout_add(milliseconds, once(callback))

    def after_idle(self, callback):
        return GLib.idle_add(once(callback))

    def after_cancel(self, source):
        GLib.source_remove(source)

    def createfilehandler(self, file, mask, callback):
        def dispatch(_fd, _condition):
            callback(file, mask)
            return file in self.watches
        self.watches[file] = GLib.unix_fd_add_full(
            GLib.PRIORITY_DEFAULT, file.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, dispatch)

    def deletefilehandler(self, file):
        source = self.watches.pop(file, None)
        if source is not None:
            GLib.source_remove(source)
