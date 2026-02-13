import logging

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Pango
from ks_includes.screen_panel import ScreenPanel

MAX_FILE_SIZE = 1024 * 1024  # 1 MB


class Panel(ScreenPanel):
    def __init__(self, screen, title, **kwargs):
        super().__init__(screen, title)
        self.filename = kwargs.get("filename", "")

        tb = Gtk.TextBuffer()
        tv = Gtk.TextView(
            buffer=tb, editable=False, cursor_visible=False,
            monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
        )

        sw = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        sw.add(tv)

        close = self._gtk.Button("cancel", _("Close"), "color2")
        close.connect("clicked", self.close_panel)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.pack_start(sw, True, True, 0)
        box.pack_end(close, False, False, 0)

        self.labels['tb'] = tb
        self.content.add(box)
        self.fetch_content()

    def fetch_content(self):
        self.labels['tb'].set_text(_("Loading..."))
        response = self._screen.apiclient.send_request(
            f"server/files/gcodes/{self.filename}", json=False,
        )
        if not response:
            self.labels['tb'].set_text(_("Error loading file"))
            return
        self.display_content(response)

    def display_content(self, data):
        truncated = len(data) > MAX_FILE_SIZE
        if truncated:
            data = data[:MAX_FILE_SIZE]
        try:
            text = data.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            try:
                text = data.decode("latin-1")
            except Exception:
                text = _("Unable to decode file content")
        if truncated:
            text += f"\n\n[{_('File truncated')} — 1 MB {_('limit')}]"
        self.labels['tb'].set_text(text)

    def close_panel(self, widget=None):
        self._screen._menu_go_back()
