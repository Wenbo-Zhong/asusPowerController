"""统一加载 GTK 4/libadwaita/Cairo 版本，供所有页面复用。"""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_foreign('cairo')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
