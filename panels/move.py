import configparser
import logging
import re

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from ks_includes.KlippyGcodes import KlippyGcodes
from ks_includes.screen_panel import ScreenPanel


def force_move_enabled(force_move_section):
    # The configfile values are the raw strings, Klipper parses them with configparser
    if not force_move_section:
        return False
    value = force_move_section.get("enable_force_move", "False").lower()
    return configparser.RawConfigParser.BOOLEAN_STATES.get(value, False)


def set_style_class(widget, name, enabled):
    if enabled:
        widget.get_style_context().add_class(name)
    else:
        widget.get_style_context().remove_class(name)


class Panel(ScreenPanel):
    distances = [".1", ".5", "1", "5", "10", "25", "50"]
    distance = distances[-2]

    def __init__(self, screen, title):
        title = title or _("Move")
        super().__init__(screen, title)

        if self.ks_printer_cfg is not None:
            dis = self.ks_printer_cfg.get("move_distances", "")
            if re.match(r"^[0-9,\.\s]+$", dis):
                dis = [str(i.strip()) for i in dis.split(",")]
                if 1 < len(dis) <= 7:
                    self.distances = dis
                    self.distance = self.distances[-2]

        self.settings = {}
        self.force_move_armed = False
        self.menu.append("move_menu")
        self.buttons = {
            "x+": self._gtk.Button("arrow-right", "X+", "color1"),
            "x-": self._gtk.Button("arrow-left", "X-", "color1"),
            "y+": self._gtk.Button("arrow-up", "Y+", "color2"),
            "y-": self._gtk.Button("arrow-down", "Y-", "color2"),
            "z+": self._gtk.Button("z-farther", "Z+", "color3"),
            "z-": self._gtk.Button("z-closer", "Z-", "color3"),
            "home": self._gtk.Button("home", _("Home"), "color4"),
            "motors_off": self._gtk.Button("motor-off", _("Disable Motors"), "color4"),
            "switch_toolhead": self._gtk.Button("toolchanger", "", "color4"),
        }
        self.buttons["x+"].connect("clicked", self.move, "X", "+")
        self.buttons["x-"].connect("clicked", self.move, "X", "-")
        self.buttons["y+"].connect("clicked", self.move, "Y", "+")
        self.buttons["y-"].connect("clicked", self.move, "Y", "-")
        self.buttons["z+"].connect("clicked", self.move, "Z", "+")
        self.buttons["z-"].connect("clicked", self.move, "Z", "-")
        self.buttons["home"].connect("clicked", self.home)
        script = {"script": "M18"}
        self.buttons["motors_off"].connect(
            "clicked",
            self._screen._confirm_send_action,
            _("Are you sure you wish to disable motors?"),
            "printer.gcode.script",
            script,
        )
        self.buttons["switch_toolhead"].connect("clicked", self.switch_toolhead)
        adjust = self._gtk.Button("settings", None, "color2", 1, Gtk.PositionType.LEFT, 1)
        adjust.connect("clicked", self.load_menu, "options", _("Settings"))
        adjust.set_hexpand(False)

        xm = "x-"
        xp = "x+"
        ym = "y-"
        yp = "y+"
        zm = "z-"
        zp = "z+"

        rotation = (
            0 if self.ks_printer_cfg is None else self.ks_printer_cfg.getint("screw_rotation", 0)
        )
        if rotation == 90:
            xm, yp, xp, ym = ym, xm, yp, xp
        elif rotation == 180:
            xm, yp, xp, ym = xp, ym, xm, yp
        elif rotation == 270:
            xm, yp, xp, ym = yp, xp, ym, xm

        if self._config.get_config()["main"].getboolean("invert_z", False):
            zm, zp = zp, zm

        scale = self._gtk.img_scale * self._gtk.button_image_scale
        self.buttons[xm].set_image(self._gtk.Image("arrow-left", scale, scale))
        self.buttons[yp].set_image(self._gtk.Image("arrow-up", scale, scale))
        self.buttons[xp].set_image(self._gtk.Image("arrow-right", scale, scale))
        self.buttons[ym].set_image(self._gtk.Image("arrow-down", scale, scale))

        grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True)
        if self._screen.vertical_mode:
            grid.attach(self.buttons[xm], 0, 1, 1, 1)
            grid.attach(self.buttons[xp], 2, 1, 1, 1)
            grid.attach(self.buttons[ym], 1, 1, 1, 1)
            grid.attach(self.buttons[yp], 1, 0, 1, 1)
            grid.attach(self.buttons[zm], 0, 2, 1, 1)
            grid.attach(self.buttons[zp], 2, 2, 1, 1)
            grid.attach(adjust, 1, 2, 1, 1)
        else:
            grid.attach(self.buttons[xm], 0, 1, 1, 1)
            grid.attach(self.buttons[xp], 2, 1, 1, 1)
            grid.attach(self.buttons[ym], 1, 1, 1, 1)
            grid.attach(self.buttons[yp], 1, 0, 1, 1)
            grid.attach(self.buttons[zm], 3, 1, 1, 1)
            grid.attach(self.buttons[zp], 3, 0, 1, 1)

        grid.attach(self.buttons["home"], 0, 0, 1, 1)

        has_dual_carriage = "dual_carriage" in self._printer.get_config_section_list()
        has_t1 = "T1" in self._printer.available_commands
        extra_button = self.buttons["motors_off"]
        if has_dual_carriage and has_t1:
            extra_button = self.buttons["switch_toolhead"]
            self.set_toolhead_label()
        grid.attach(extra_button, 2, 0, 1, 1)

        distgrid = Gtk.Grid()
        for j, i in enumerate(self.distances):
            self.labels[i] = self._gtk.Button(label=i)
            self.labels[i].set_direction(Gtk.TextDirection.LTR)
            self.labels[i].connect("clicked", self.change_distance, i)
            ctx = self.labels[i].get_style_context()
            ctx.add_class("horizontal_togglebuttons")
            if i == self.distance:
                ctx.add_class("horizontal_togglebuttons_active")
            distgrid.attach(self.labels[i], j, 0, 1, 1)

        for p in ("pos_x", "pos_y", "pos_z"):
            self.labels[p] = self._gtk.Button()
            self.labels[p].set_hexpand(False)
            self.labels[p].set_vexpand(True)
            self.labels[p].connect("clicked", self.menu_item_clicked, {"panel": "move_advanced"})
            self.labels[p].get_style_context().add_class("no-margin")
        self.labels["move_dist"] = Gtk.Label(label=_("Move Distance (mm)"), hexpand=True)
        self.buttons["force_move"] = self.force_move_button()
        dist_row = Gtk.Box()
        dist_row.add(self.labels["move_dist"])
        # Alone in its box the toggle is first and last child, so it gets both rounded ends
        force_move_box = Gtk.Box()
        force_move_box.add(self.buttons["force_move"])
        dist_row.add(force_move_box)

        bottomgrid = Gtk.Grid(column_homogeneous=True)
        bottomgrid.set_row_spacing(0)
        bottomgrid.get_style_context().add_class("no-margin")
        bottomgrid.get_style_context().add_class("no-padding")
        bottomgrid.attach(self.labels["pos_x"], 0, 0, 1, 1)
        bottomgrid.attach(self.labels["pos_y"], 1, 0, 1, 1)
        bottomgrid.attach(self.labels["pos_z"], 2, 0, 1, 1)
        bottomgrid.attach(dist_row, 0, 1, 3, 1)
        if not self._screen.vertical_mode:
            bottomgrid.attach(adjust, 3, 0, 1, 2)

        self.labels["move_menu"] = Gtk.Grid(row_homogeneous=True, column_homogeneous=True)
        self.labels["move_menu"].attach(grid, 0, 0, 1, 3)
        self.labels["move_menu"].attach(bottomgrid, 0, 3, 1, 1)
        self.labels["move_menu"].attach(distgrid, 0, 4, 1, 1)

        self.content.add(self.labels["move_menu"])

        printer_cfg = self._printer.get_config_section("printer")
        # The max_velocity parameter is not optional in klipper config.
        # The minimum is 1, but least 2 values are needed to create a scale
        max_velocity = max(int(float(printer_cfg["max_velocity"])), 2)
        if "max_z_velocity" in printer_cfg:
            self.max_z_velocity = max(int(float(printer_cfg["max_z_velocity"])), 2)
        else:
            self.max_z_velocity = max_velocity

        configurable_options = [
            {
                "invert_x": {
                    "section": "main",
                    "name": _("Invert X"),
                    "type": "binary",
                    "tooltip": _("This will affect screw positions and mesh graph"),
                    "value": "False",
                    "callback": self.reinit_panels,
                }
            },
            {
                "invert_y": {
                    "section": "main",
                    "name": _("Invert Y"),
                    "type": "binary",
                    "tooltip": _("This will affect screw positions and mesh graph"),
                    "value": "False",
                    "callback": self.reinit_panels,
                }
            },
            {
                "invert_z": {
                    "section": "main",
                    "name": _("Invert Z"),
                    "tooltip": _(
                        "Swaps buttons if they are on top of each other, affects other panels"
                    ),
                    "type": "binary",
                    "value": "False",
                    "callback": self.reinit_move,
                }
            },
            {
                "move_speed_xy": {
                    "section": "main",
                    "name": _("XY Speed (mm/s)"),
                    "type": "scale",
                    "tooltip": _("Only for the move panel"),
                    "value": "50",
                    "range": [1, max_velocity],
                    "step": 1,
                }
            },
            {
                "move_speed_z": {
                    "section": "main",
                    "name": _("Z Speed (mm/s)"),
                    "type": "scale",
                    "tooltip": _("Only for the move panel"),
                    "value": "10",
                    "range": [1, self.max_z_velocity],
                    "step": 1,
                }
            },
        ]

        self.labels["options_menu"] = self._gtk.ScrolledWindow()
        self.labels["options"] = Gtk.Grid()
        self.labels["options_menu"].add(self.labels["options"])
        self.options = {}
        for option in configurable_options:
            name = list(option)[0]
            self.options.update(self.add_option("options", self.settings, name, option[name]))

    def reinit_panels(self, value):
        self._screen.panels_reinit.append("bed_level")
        self._screen.panels_reinit.append("bed_mesh")

    def reinit_move(self, widget):
        self._screen.panels_reinit.append("move")
        self._screen.panels_reinit.append("zcalibrate")
        self.menu.clear()

    def set_toolhead_label(self):
        if "switch_toolhead" not in self.buttons:
            return
        current = self._printer.get_stat("toolhead", "extruder")
        if current and current.startswith("extruder"):
            t_num = current[8:] or "0"
            self.buttons["switch_toolhead"].set_label(f"T{t_num}")

    def process_update(self, action, data):
        if action != "notify_status_update":
            return
        if (
            "toolhead" in data
            and "extruder" in data["toolhead"]
            and "switch_toolhead" in self.buttons
        ):
            self.set_toolhead_label()
        if "toolhead" in data and "max_velocity" in data["toolhead"]:
            max_vel = max(int(float(data["toolhead"]["max_velocity"])), 2)
            adj = self.options["move_speed_xy"].get_adjustment()
            adj.set_upper(max_vel)
        if "gcode_move" in data or "toolhead" in data and "homed_axes" in data["toolhead"]:
            homed_axes = self._printer.get_stat("toolhead", "homed_axes")
            for i, axis in enumerate(("x", "y", "z")):
                if axis not in homed_axes:
                    self.labels[f"pos_{axis}"].set_label(f"{axis.upper()}: ?")
                elif "gcode_move" in data and "gcode_position" in data["gcode_move"]:
                    self.labels[f"pos_{axis}"].set_label(
                        f"{axis.upper()}: {data['gcode_move']['gcode_position'][i]:.2f}"
                    )
        if "toolhead" in data and "homed_axes" in data["toolhead"]:
            self.update_force_move()

    def deactivate(self):
        self.force_move_armed = False
        self.show_force_move_state(())

    def change_distance(self, widget, distance):
        logging.info(f"### Distance {distance}")
        self.labels[f"{self.distance}"].get_style_context().remove_class(
            "horizontal_togglebuttons_active"
        )
        self.labels[f"{distance}"].get_style_context().add_class("horizontal_togglebuttons_active")
        self.distance = distance

    def move(self, widget, axis, direction):
        axis = axis.lower()
        if self._config.get_config()["main"].getboolean(f"invert_{axis}", False) and axis != "z":
            direction = "-" if direction == "+" else "+"

        dist = f"{direction}{self.distance}"
        config_key = "move_speed_z" if axis == "z" else "move_speed_xy"
        speed = (
            None if self.ks_printer_cfg is None else self.ks_printer_cfg.getint(config_key, None)
        )
        if speed is None:
            speed = self._config.get_config()["main"].getint(config_key, self.max_z_velocity)
        speed = max(1, speed)
        if self.force_move_armed and self.can_force_move(axis):
            self.force_move(widget, axis, dist, speed)
            return
        script = f"{KlippyGcodes.MOVE_RELATIVE}\nG0 {axis}{dist} F{60 * speed}"
        self._screen._send_action(widget, "printer.gcode.script", {"script": script})
        if self._printer.get_stat("gcode_move", "absolute_coordinates"):
            self._screen._ws.api.gcode_script("G90")

    def can_force_move(self, axis):
        # FORCE_MOVE neither checks nor updates the toolhead position, so only an
        # unhomed axis may use it
        homed_axes = self._printer.get_stat("toolhead", "homed_axes")
        return axis not in homed_axes and self.force_move_configured(axis)

    def force_move_configured(self, axis):
        # Only where FORCE_MOVE moves the one stepper of that axis
        return (
            force_move_enabled(self._printer.get_config_section("force_move"))
            and self._printer.get_config_section("printer")["kinematics"] == "cartesian"
            and not self.has_second_stepper(axis)
        )

    def force_move_button(self):
        button = self._gtk.Button(
            "warning", " " + _("Unhomed moves"), None, self.bts, Gtk.PositionType.LEFT, 1
        )
        button.set_hexpand(False)
        # update_force_move decides the visibility, the show_all of the panel must not
        button.set_no_show_all(True)
        button.get_style_context().add_class("horizontal_togglebuttons")
        button.get_style_context().add_class("horizontal_togglebuttons_smaller")
        button.connect("clicked", self.toggle_force_move)
        return button

    def toggle_force_move(self, widget):
        self.force_move_armed = not self.force_move_armed
        self.update_force_move()

    def update_force_move(self):
        configured = [axis for axis in "xyz" if self.force_move_configured(axis)]
        movable = [axis for axis in configured if self.can_force_move(axis)]
        self.force_move_armed = self.force_move_armed and bool(movable)
        self.buttons["force_move"].set_visible(bool(configured))
        self.buttons["force_move"].set_sensitive(bool(movable))
        self.show_force_move_state(movable)

    def show_force_move_state(self, movable):
        armed = self.force_move_armed
        set_style_class(self.buttons["force_move"], "horizontal_togglebuttons_active", armed)
        # invalid is the alert style of a button, and every theme defines it
        for axis in "xyz":
            for direction in "+-":
                button = self.buttons[f"{axis}{direction}"]
                set_style_class(button, "invalid", armed and axis in movable)

    def has_second_stepper(self, axis):
        dual_carriage = self._printer.get_config_section("dual_carriage") or {}
        return (
            self._printer.config_section_exists(f"stepper_{axis}1")
            or dual_carriage.get("axis") == axis
        )

    def force_move(self, widget, axis, dist, speed):
        # Klipper queues a command sent while another one runs, so a move sent during
        # G28 would run after the axis is homed, at a position Klipper no longer tracks
        if self._printer.get_stat("idle_timeout", "state") == "Printing":
            self._screen.show_popup_message(_("Printer busy, unhomed move not sent"), level=2)
            return
        max_velocity, accel = self.force_move_limits(axis)
        script = (
            f"FORCE_MOVE STEPPER=stepper_{axis} DISTANCE={dist} "
            f"VELOCITY={min(speed, max_velocity)} ACCEL={accel}"
        )
        self._screen._send_action(widget, "printer.gcode.script", {"script": script})

    def force_move_limits(self, axis):
        if axis != "z":
            toolhead = self._printer.get_stat("toolhead")
            return toolhead["max_velocity"], toolhead["max_accel"]
        # Klipper defaults max_z_velocity and max_z_accel to max_velocity and max_accel
        printer_cfg = self._printer.get_config_section("printer")
        return (
            float(printer_cfg.get("max_z_velocity", printer_cfg["max_velocity"])),
            float(printer_cfg.get("max_z_accel", printer_cfg["max_accel"])),
        )

    def home(self, widget):
        if "delta" in self._printer.get_config_section("printer")["kinematics"]:
            self._screen._send_action(widget, "printer.gcode.script", {"script": "G28"})
            return
        name = "homing"
        disname = self._screen._config.get_menu_name("move", name)
        menuitems = self._screen._config.get_menu_items("move", name)
        self._screen.show_panel("menu", disname, items=menuitems)

    def switch_toolhead(self, widget):
        current = self._printer.get_stat("toolhead", "extruder")
        is_t1 = current and current.startswith("extruder1")
        script = {"script": "T0" if is_t1 else "T1"}
        self._screen._send_action(widget, "printer.gcode.script", script)
