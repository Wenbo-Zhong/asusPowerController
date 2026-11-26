"""Load the GTK versions used by every view."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_foreign('cairo')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
