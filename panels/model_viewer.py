import functools
import logging
import os
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango

from ks_includes import model_geometry
from ks_includes.files import KlippyFiles
from ks_includes.screen_panel import ScreenPanel
from ks_includes.widgets.model_view import ModelView

DOWNLOAD_TIMEOUT = 30
# Parsing a large G-code file keeps a core busy for seconds.
# A higher niceness lets Klipper go first.
WORKER_NICENESS = 10
# Seconds between parsing progress updates sent to the main loop.
PROGRESS_INTERVAL = 0.2
# Zoom of one tap on a zoom button, as a pinch scale.
ZOOM_STEP = 1.25


def lower_thread_priority():
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), WORKER_NICENESS)
    except (AttributeError, OSError) as error:
        logging.debug(f"Unable to lower the model loading priority: {error}")


def parse_model(path, data, bed, cancelled, progress):
    if KlippyFiles.is_stl(path):
        return model_geometry.parse_stl(data, model_geometry.bed_center(bed))
    return model_geometry.parse_gcode(data, cancelled, progress)


def throttled(callback, interval):
    """callback, minus the calls that come within interval seconds of the last one passed on."""
    last_call = None

    def call(*arguments):
        nonlocal last_call
        now = time.monotonic()
        if last_call is not None and now - last_call < interval:
            return
        last_call = now
        callback(*arguments)

    return call


def parsing_text(fraction):
    return f"{_('Parsing')} {fraction:.0%}"


def layer_text(layer, count, height):
    return f"{_('Layer:')}\n{layer}/{count}\n{height:.2f} {_('mm')}"


def set_style_class(widget, name, enabled):
    if enabled:
        widget.get_style_context().add_class(name)
    else:
        widget.get_style_context().remove_class(name)


class Panel(ScreenPanel):
    """3D view of the STL or G-code file passed as extra.

    The model is freed when the panel leaves the screen.
    """

    def __init__(self, screen, title, **kwargs):
        super().__init__(screen, title)
        self.path = None
        # show_panel can run __init__ again on this instance while a worker from before still
        # runs, so the load generation must not start over.
        self.generation = getattr(self, "generation", 0)
        self.cancel_event = threading.Event()
        self.view = ModelView()
        self.view.connect_after("realize", self.on_view_realized)
        # GtkOverlay gives a wrapping label at least its height at one character per line, and
        # with the default fill alignment the text sinks below the view. Centred, it stays visible.
        self.status = Gtk.Label(
            wrap=True,
            wrap_mode=Pango.WrapMode.WORD_CHAR,
            justify=Gtk.Justification.CENTER,
            valign=Gtk.Align.CENTER,
        )
        self.status.set_no_show_all(True)
        size = self._gtk.img_scale * self._gtk.button_image_scale
        self.spinner = Gtk.Spinner(width_request=size, height_request=size)
        self.progress = Gtk.ProgressBar(show_text=True, width_request=self._screen.width // 2)
        self.loading_box = self.create_loading_box()
        self.layer_label = Gtk.Label(justify=Gtk.Justification.CENTER)
        self.layer_scale = self.create_layer_scale()
        self.slider_box = self.create_slider_box()
        self.pan_button = self.create_pan_button()
        self.controls = self.create_controls()
        self.content.add(self.create_layout())

    def create_layer_scale(self):
        """Beside the view in landscape, top layer at the top. Below the view in vertical mode."""
        upright = not self._screen.vertical_mode
        scale = Gtk.Scale(
            orientation=Gtk.Orientation.VERTICAL if upright else Gtk.Orientation.HORIZONTAL,
            inverted=upright,
            draw_value=False,
            digits=0,
            vexpand=upright,
            hexpand=not upright,
        )
        scale.connect("value-changed", self.on_layer_changed)
        return scale

    def create_slider_box(self):
        orientation = (
            Gtk.Orientation.HORIZONTAL if self._screen.vertical_mode else Gtk.Orientation.VERTICAL
        )
        box = Gtk.Box(orientation=orientation, spacing=5)
        box.pack_start(self.layer_label, False, False, 5)
        box.pack_start(self.layer_scale, True, True, 0)
        box.show_all()
        box.set_no_show_all(True)
        box.hide()
        return box

    def create_controls(self):
        """Zoom buttons and the pan toggle. A row below the view in vertical mode, else a column."""
        vertical = self._screen.vertical_mode
        zoom_out = self._gtk.Button(
            "decrease", " " + _("Zoom out"), "color3", self.bts, Gtk.PositionType.LEFT, 1
        )
        zoom_out.connect("clicked", self.on_zoom, 1 / ZOOM_STEP)
        zoom_in = self._gtk.Button(
            "increase", " " + _("Zoom in"), "color1", self.bts, Gtk.PositionType.LEFT, 1
        )
        zoom_in.connect("clicked", self.on_zoom, ZOOM_STEP)
        # Alone in its box the toggle is first and last child, so it gets both rounded ends
        pan_box = Gtk.Box()
        pan_box.add(self.pan_button)
        # The buttons expand both ways, the box must not take space from the view
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL if vertical else Gtk.Orientation.VERTICAL,
            spacing=5,
            homogeneous=True,
            hexpand=False,
            vexpand=False,
            valign=Gtk.Align.FILL if vertical else Gtk.Align.START,
        )
        for widget in (zoom_out, zoom_in, pan_box):
            box.add(widget)
        box.show_all()
        box.set_no_show_all(True)
        box.hide()
        return box

    def create_pan_button(self):
        """A toggle in KlipperScreen's toggle button style, filled while a drag pans."""
        button = self._gtk.Button("move", " " + _("Pan"), None, self.bts, Gtk.PositionType.LEFT, 1)
        # The toggle style has no vertical margin, so filling the row it would stand taller than
        # the zoom buttons
        button.set_valign(Gtk.Align.CENTER)
        button.get_style_context().add_class("horizontal_togglebuttons")
        button.get_style_context().add_class("horizontal_togglebuttons_smaller")
        button.connect("clicked", self.on_pan_clicked)
        return button

    def create_loading_box(self):
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        box.pack_start(self.spinner, False, False, 0)
        box.pack_start(self.progress, False, False, 0)
        box.show_all()
        box.set_no_show_all(True)
        box.hide()
        return box

    def create_layout(self):
        overlay = Gtk.Overlay()
        overlay.add(self.view)
        overlay.add_overlay(self.loading_box)
        overlay.add_overlay(self.status)
        orientation = (
            Gtk.Orientation.VERTICAL if self._screen.vertical_mode else Gtk.Orientation.HORIZONTAL
        )
        layout = Gtk.Box(orientation=orientation, spacing=5)
        layout.pack_start(overlay, True, True, 0)
        layout.pack_start(self.slider_box, False, False, 0)
        layout.pack_start(self.controls, False, False, 0)
        return layout

    def set_extra(self, extra=None, **kwargs):
        """Called by show_panel before activate, also when the cached panel shows another file."""
        self.path = extra
        self.title = os.path.basename(extra or "")

    def activate(self):
        if self.view.failure is not None:
            self.show_status(self.view.failure)
        elif self.path:
            self.start_loading()

    def on_view_realized(self, view):
        """Without OpenGL the view stays empty, so show why instead of loading."""
        if view.failure is not None:
            self.generation += 1
            self.cancel_event.set()
            self.show_failure(self.generation, view.failure)

    def deactivate(self):
        self.generation += 1
        self.cancel_event.set()
        self.view.clear()
        self.hide_loading()
        self.slider_box.hide()
        self.controls.hide()
        self.set_panning(False)

    def bed_bounds(self):
        return model_geometry.bed_bounds(
            self._printer.get_stat("toolhead", "axis_minimum"),
            self._printer.get_stat("toolhead", "axis_maximum"),
        )

    def show_status(self, text):
        self.status.set_text(text)
        self.status.show()

    def show_loading(self, text, fraction):
        self.progress.set_text(text)
        self.progress.set_fraction(fraction)
        self.spinner.start()
        self.loading_box.show()

    def hide_loading(self):
        self.spinner.stop()
        self.loading_box.hide()

    def show_progress(self, generation, text, fraction):
        if generation == self.generation:
            self.show_loading(text, fraction)
        return False

    def start_loading(self):
        self.generation += 1
        self.cancel_event = threading.Event()
        self.status.hide()
        self.show_loading(_("Downloading"), 0.0)
        arguments = (self.generation, self.path, self.bed_bounds(), self.cancel_event)
        threading.Thread(target=self.load, args=arguments, daemon=True).start()

    def load(self, generation, path, bed, cancel_event):
        """Worker thread: download and parse, then hand the result to the main loop."""
        lower_thread_priority()
        data = self._screen.files.download(path, timeout=DOWNLOAD_TIMEOUT)
        if data is None:
            GLib.idle_add(self.show_failure, generation, _("Error loading file"))
            return
        GLib.idle_add(self.show_progress, generation, _("Parsing"), 0.0)
        progress = throttled(functools.partial(self.report_parsing, generation), PROGRESS_INTERVAL)
        try:
            model = parse_model(path, data, bed, cancel_event.is_set, progress)
        except model_geometry.Cancelled:
            return
        except Exception as error:
            logging.exception(f"Unable to show {path}")
            GLib.idle_add(
                self.show_failure, generation, f"{_('Unable to show this file')}\n{error}"
            )
            return
        GLib.idle_add(self.show_loaded, generation, model, bed)

    def report_parsing(self, generation, done, total):
        """Worker thread: hand the parsing progress to the main loop."""
        fraction = model_geometry.progress_fraction(done, total)
        GLib.idle_add(self.show_progress, generation, parsing_text(fraction), fraction)

    def show_failure(self, generation, text):
        if generation == self.generation:
            self.hide_loading()
            self.show_status(text)
        return False

    def show_loaded(self, generation, model, bed):
        if generation != self.generation:
            return False
        self.hide_loading()
        self.view.show_model(model, bed)
        self.setup_layer_slider(model)
        self.controls.show()
        return False

    def on_zoom(self, button, scale):
        self.view.zoom_by(scale)

    def on_pan_clicked(self, button):
        self.set_panning(not self.view.drag_pans)

    def set_panning(self, active):
        self.view.drag_pans = active
        set_style_class(self.pan_button, "horizontal_togglebuttons_active", active)

    def setup_layer_slider(self, model):
        count = len(model.layer_ends) if isinstance(model, model_geometry.Toolpath) else 0
        if count < 2:
            self.slider_box.hide()
            return
        self.layer_scale.set_range(1, count)
        self.layer_scale.set_increments(1, 10)
        self.layer_scale.set_value(count)
        self.on_layer_changed(self.layer_scale)
        self.slider_box.show()

    def on_layer_changed(self, scale):
        model = self.view.model
        if not isinstance(model, model_geometry.Toolpath):
            return
        layer = int(scale.get_value())
        self.view.set_top_layer(layer)
        self.layer_label.set_text(
            layer_text(layer, len(model.layer_ends), model.layer_heights[layer - 1])
        )
