"""GNOME session idle detection and keyboard backlight timeout."""

IDLE = 'org.gnome.Mutter.IdleMonitor'
POWER = 'org.gnome.SettingsDaemon.Power'
KEYBOARD = POWER + '.Keyboard'
POWER_PATH = '/org/gnome/SettingsDaemon/Power'
PROPERTIES = 'org.freedesktop.DBus.Properties'


class KeyboardIdle:
    def __init__(self, connection, gio, glib):
        self.connection = connection
        self.gio = gio
        self.glib = glib
        self.restore_level = None

    def call(self, service, path, interface, method, parameters=None):
        return self.connection.call_sync(
            service, path, interface, method, parameters, None,
            self.gio.DBusCallFlags.NO_AUTO_START, 750, None,
        ).unpack()

    def brightness(self):
        return self.call(POWER, POWER_PATH, PROPERTIES, 'Get',
                         self.glib.Variant('(ss)', (KEYBOARD, 'Brightness')))[0]

    def set_brightness(self, level):
        self.call(POWER, POWER_PATH, PROPERTIES, 'Set',
                  self.glib.Variant('(ssv)', (KEYBOARD, 'Brightness', self.glib.Variant('i', level))))

    def restore(self):
        if self.restore_level is not None:
            if self.brightness() == 0:
                self.set_brightness(self.restore_level)
            self.restore_level = None

    def update(self, seconds):
        if not seconds:
            self.restore()
            return
        idle, = self.call(IDLE, '/org/gnome/Mutter/IdleMonitor/Core', IDLE, 'GetIdletime')
        if idle < seconds * 1000:
            self.restore()
        elif self.restore_level is None:
            level = self.brightness()
            if level > 0:
                self.set_brightness(0)
                self.restore_level = level
