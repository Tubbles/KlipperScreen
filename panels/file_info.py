import os

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
from datetime import datetime
from ks_includes.screen_panel import ScreenPanel


class Panel(ScreenPanel):
    def __init__(self, screen, title, **kwargs):
        super().__init__(screen, title)
        self.filename = kwargs.get("filename", "")
        item = kwargs.get("item", {})
        time_24 = self._config.get_main_config().getboolean("24htime", True)

        name = os.path.basename(self.filename)
        ext = os.path.splitext(name)[1]

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=10,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
            vexpand=True, hexpand=True,
        )

        name_label = Gtk.Label()
        name_label.set_markup(f"<big><b>{name}</b></big>")
        name_label.set_line_wrap(True)
        box.add(name_label)

        if "size" in item:
            box.add(Gtk.Label(label=f"{_('Size')}: {self.format_size(item['size'])}"))

        if "modified" in item:
            if time_24:
                date_str = f"{datetime.fromtimestamp(item['modified']):%Y/%m/%d %H:%M}"
            else:
                date_str = f"{datetime.fromtimestamp(item['modified']):%Y/%m/%d %I:%M %p}"
            box.add(Gtk.Label(label=f"{_('Modified')}: {date_str}"))

        if ext:
            box.add(Gtk.Label(label=f"{_('Type')}: {ext}"))

        close = self._gtk.Button("cancel", _("Close"), "color2")
        close.connect("clicked", self.close_panel)
        box.add(close)

        self.content.add(box)

    def close_panel(self, widget=None):
        self._screen._menu_go_back()
