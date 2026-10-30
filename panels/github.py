import logging
from datetime import datetime, timezone

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk, Pango

from ks_includes import github_slicing as slicing
from ks_includes.screen_panel import ScreenPanel

# Seconds between asking Moonraker whether a finished gcode has reached the printer.
POLL_SECONDS = 2


def thumbnail_pixbuf(data, size):
    """PNG bytes as a pixbuf that fits size, None when GdkPixbuf cannot read them."""
    stream = Gio.MemoryInputStream.new_from_data(data, None)
    try:
        return GdkPixbuf.Pixbuf.new_from_stream_at_scale(stream, int(size), int(size), True)
    except GLib.Error as error:
        logging.error(f"Unreadable thumbnail: {error}")
        return None
    finally:
        stream.close()


def bold_markup(text):
    return f"<big><b>{GLib.markup_escape_text(text)}</b></big>"


def title_label(text):
    label = Gtk.Label(halign=Gtk.Align.START, xalign=0, ellipsize=Pango.EllipsizeMode.END)
    label.set_markup(bold_markup(text))
    return label


def wrapping_label(**kwargs):
    return Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, **kwargs)


def set_style_class(widget, name, enabled):
    if enabled:
        widget.get_style_context().add_class(name)
    else:
        widget.get_style_context().remove_class(name)


def clear_container(container):
    for child in container.get_children():
        container.remove(child)


class Panel(ScreenPanel):
    """Slices a model from a CAD repository's CI artifacts on the slicing agent, then prints it.

    The panel keeps its own back stack of four views: projects, models, options and the job.
    """

    def __init__(self, screen, title):
        super().__init__(screen, title or "GitHub")
        # show_panel can run __init__ again on this instance while answers from before are still
        # on their way, so the token that drops them must not start over.
        self.token = getattr(self, "token", 0) + 1
        self.poll_source = None
        self.checking_file = False
        self.projects = None
        self.project = None
        self.models = None
        self.thumbnail_queue = []
        self.thumbnail_images = {}
        self.thumbnail_size = self._gtk.img_scale * self._gtk.button_image_scale * 1.25
        self.model = None
        self.options = None
        self.choice = None
        self.results = []
        self.slice_pending = False
        self.rule_rows = {}
        self.option_buttons = {}
        self.job = None
        self.file_ready = False
        self.views = [("projects", self.create_projects_view())]

    # Views

    def activate(self):
        self.enter_view()

    def deactivate(self):
        self.leave_view()

    def back(self):
        if len(self.views) == 1:
            return False
        self.views.pop()
        self.enter_view()
        return True

    def show_view(self, name, view):
        self.views.append((name, view))
        self.enter_view()

    def leave_view(self):
        """Drop the answers and stop the polling of the view on screen."""
        self.token += 1
        if self.poll_source is not None:
            GLib.source_remove(self.poll_source)
            self.poll_source = None
        self.checking_file = False

    def enter_view(self):
        self.leave_view()
        name, view = self.views[-1]
        clear_container(self.content)
        self.content.add(view)
        self.content.show_all()
        self._screen.base_panel.set_title(self.view_title(name))
        resume = {
            "projects": self.resume_projects,
            "models": self.resume_models,
            "options": self.resume_options,
            "job": self.resume_job,
        }
        resume[name]()

    def view_title(self, name):
        if name == "projects":
            return self.title
        detail = self.project.name if name == "models" else self.model
        return f"{self.title} | {detail}"

    # Requests

    def send(self, method, params, callback, *args):
        """Send a request whose answer reaches callback only while the sending view is shown."""
        self._screen._ws.send_method(method, params, self.on_answer, self.token, callback, *args)

    def call_agent(self, name, arguments, callback, *args):
        self.send(
            "server.extensions.request", slicing.agent_request(name, arguments), callback, *args
        )

    def on_answer(self, response, method, params, token, callback, *args):
        if token == self.token:
            callback(response, *args)

    # Shared widgets

    def create_status(self, key):
        label = wrapping_label(no_show_all=True)
        self.labels[f"{key}_status"] = label
        return label

    def set_status(self, key, text):
        label = self.labels[f"{key}_status"]
        label.set_text(text)
        label.set_visible(bool(text))

    def create_scroll(self, child):
        scroll = self._gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(child)
        return scroll

    def create_list_view(self, key, header=None):
        self.labels[f"{key}_list"] = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        if header is not None:
            view.pack_start(header, False, False, 0)
        view.pack_start(self.create_status(key), False, False, 0)
        view.pack_start(self.create_scroll(self.labels[f"{key}_list"]), True, True, 0)
        return view

    def fill_list(self, key, rows, empty_text):
        box = self.labels[f"{key}_list"]
        clear_container(box)
        for row in rows:
            box.add(row)
        box.show_all()
        self.set_status(key, "" if rows else empty_text)

    def create_row(self, title, detail, callback, *args, image=None):
        """A row to tap: the title label over a detail line, after an optional image."""
        labels = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True
        )
        labels.add(title)
        labels.add(wrapping_label(label=detail, halign=Gtk.Align.START, xalign=0))
        box = Gtk.Box(spacing=5)
        box.get_style_context().add_class("frame-item")
        if image is not None:
            box.add(image)
        box.add(labels)
        row = self._gtk.Button()
        row.set_vexpand(False)
        row.add(box)
        row.connect("clicked", callback, *args)
        return row

    # Projects

    def create_projects_view(self):
        refresh = self._gtk.Button(
            "refresh", _("Refresh"), "color3", self.bts, Gtk.PositionType.LEFT, 1
        )
        refresh.get_style_context().add_class("buttons_slim")
        refresh.set_vexpand(False)
        refresh.connect("clicked", self.load_projects)
        self.labels["refresh"] = refresh
        return self.create_list_view("projects", refresh)

    def resume_projects(self):
        if self.projects is None:
            self.load_projects()

    def load_projects(self, widget=None):
        self.projects = None
        self._gtk.Button_busy(self.labels["refresh"], True)
        self.set_status("projects", _("Loading..."))
        self.call_agent("projects", {}, self.on_projects)

    def on_projects(self, response):
        self._gtk.Button_busy(self.labels["refresh"], False)
        error = slicing.response_error(response)
        if error is not None:
            self.set_status("projects", error)
            return
        self.projects = slicing.parse_projects(response["result"])
        now = datetime.now(timezone.utc)
        rows = [self.create_project_row(project, now) for project in self.projects]
        self.fill_list("projects", rows, _("No projects"))

    def create_project_row(self, project, now):
        age = slicing.format_age(slicing.age_seconds(project.created_at, now))
        size = self.format_size(project.size)
        detail = " · ".join((slicing.short_commit(project.commit), age, size))
        return self.create_row(title_label(project.name), detail, self.open_project, project)

    def open_project(self, widget, project):
        self.project = project
        self.models = None
        self.show_view("models", self.create_list_view("models"))

    # Models

    def resume_models(self):
        if self.models is None:
            self.load_models()
        else:
            self.load_next_thumbnail()

    def load_models(self):
        self.set_status("models", _("Loading..."))
        self.call_agent("models", {"artifact_id": self.project.artifact_id}, self.on_models)

    def on_models(self, response):
        error = slicing.response_error(response)
        if error is not None:
            self.set_status("models", error)
            return
        self.models = slicing.parse_models(response["result"])
        self.thumbnail_images = {}
        rows = [self.create_model_row(model) for model in self.models]
        self.fill_list("models", rows, _("No models"))
        self.thumbnail_queue = [model.name for model in self.models]
        self.load_next_thumbnail()

    def create_model_row(self, model):
        image = self._gtk.Image("file", self.thumbnail_size, self.thumbnail_size)
        self.thumbnail_images[model.name] = image
        size = self.format_size(model.size)
        return self.create_row(
            title_label(model.name), size, self.open_model, model.name, image=image
        )

    def load_next_thumbnail(self):
        """Thumbnails render on the first request, which takes seconds, so they load one by one."""
        if not self.thumbnail_queue:
            return
        name = self.thumbnail_queue[0]
        arguments = slicing.model_arguments(self.project.artifact_id, name)
        self.call_agent("thumbnail", arguments, self.on_thumbnail, name)

    def on_thumbnail(self, response, name):
        self.thumbnail_queue.remove(name)
        error = slicing.response_error(response)
        if error is None:
            self.show_thumbnail(name, slicing.decode_thumbnail(response["result"]))
        else:
            logging.info(f"No thumbnail for {name}: {error}")
        self.load_next_thumbnail()

    def show_thumbnail(self, name, data):
        pixbuf = thumbnail_pixbuf(data, self.thumbnail_size) if data else None
        if pixbuf is not None:
            self.thumbnail_images[name].set_from_pixbuf(pixbuf)

    # Options

    def open_model(self, widget, model):
        self.model = model
        self.options = None
        self.choice = None
        self.results = []
        self.show_view("options", self.create_options_view())

    def create_options_view(self):
        self.labels["options_box"] = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.labels["slice"] = self._gtk.Button(label=_("Slice"), style="color1")
        self.labels["slice"].set_vexpand(False)
        self.labels["slice"].set_sensitive(False)
        self.labels["slice"].connect("clicked", self.start_slice)
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        view.pack_start(self.create_status("options"), False, False, 0)
        view.pack_start(self.create_scroll(self.labels["options_box"]), True, True, 0)
        view.pack_start(self.labels["slice"], False, False, 0)
        return view

    def resume_options(self):
        self.slice_pending = False
        if self.options is None:
            self.load_options()
        else:
            self.load_results()

    def load_options(self):
        self.set_status("options", _("Loading..."))
        self.call_agent("options", {"artifact_id": self.project.artifact_id}, self.on_options)

    def on_options(self, response):
        error = slicing.response_error(response)
        if error is not None:
            self.set_status("options", error)
            return
        self.options = slicing.parse_options(response["result"])
        self.choice = slicing.initial_choice(self.options)
        self.fill_options()
        self.load_results()

    def load_results(self):
        arguments = slicing.model_arguments(self.project.artifact_id, self.model)
        self.call_agent("sliced", arguments, self.on_results)

    def on_results(self, response):
        error = slicing.response_error(response)
        self.results = [] if error is not None else slicing.parse_results(response["result"])
        self.set_status("options", error or "")
        self.render_options()

    def fill_options(self):
        box = self.labels["options_box"]
        clear_container(box)
        self.rule_rows = {}
        self.option_buttons = {}
        if self.options.rules:
            box.add(title_label(_("Rules")))
        for rule in self.options.rules:
            box.add(self.create_rule_row(rule))
        if self.options.matrix:
            box.add(self.create_matrix())
        box.show_all()

    def create_rule_row(self, rule):
        label = title_label(rule.name)
        row = self.create_row(label, rule.description, self.pick_rule, rule.name)
        self.rule_rows[rule.name] = (row, label)
        return row

    def create_matrix(self):
        grid = Gtk.Grid(row_spacing=5, column_spacing=5)
        for index, dimension in enumerate(self.options.dimensions):
            label = Gtk.Label(label=dimension.name.capitalize(), halign=Gtk.Align.START)
            grid.attach(label, 0, index, 1, 1)
            grid.attach(self.create_dimension_buttons(dimension), 1, index, 1, 1)
        return grid

    def create_dimension_buttons(self, dimension):
        grid = Gtk.Grid(hexpand=True, column_homogeneous=True)
        for column, option in enumerate(dimension.options):
            button = self._gtk.Button(label=option)
            button.set_vexpand(False)
            button.get_style_context().add_class("horizontal_togglebuttons")
            button.connect("clicked", self.pick_option, dimension.name, option)
            self.option_buttons[(dimension.name, option)] = button
            grid.attach(button, column, 0, 1, 1)
        return grid

    def pick_rule(self, widget, rule):
        self.choice = slicing.choose_rule(self.choice, rule)
        self.render_options()

    def pick_option(self, widget, dimension, option):
        self.choice = slicing.choose_option(self.choice, dimension, option)
        self.render_options()

    def render_options(self):
        self.render_rules()
        self.render_matrix()
        self.render_slice_button()

    def render_rules(self):
        for name, (row, label) in self.rule_rows.items():
            set_style_class(row, "button_active", self.choice.rule == name)
            label.set_markup(
                bold_markup(slicing.marked(name, slicing.rule_sliced(name, self.results)))
            )

    def render_matrix(self):
        for (dimension, option), button in self.option_buttons.items():
            active = slicing.option_active(self.choice, dimension, option)
            set_style_class(button, "horizontal_togglebuttons_active", active)
            sliced = slicing.option_sliced(self.choice, self.results, dimension, option)
            button.set_label(slicing.marked(option, sliced))

    def render_slice_button(self):
        sliced = slicing.choice_sliced(self.choice, self.options, self.results)
        self.labels["slice"].set_label(slicing.slice_label(sliced))
        ready = slicing.slice_choice(self.choice, self.options) is not None
        self.labels["slice"].set_sensitive(ready and not self.slice_pending)

    def start_slice(self, widget):
        part = slicing.slice_choice(self.choice, self.options)
        arguments = slicing.slice_arguments(self.project.artifact_id, self.model, part)
        self.slice_pending = True
        self.render_slice_button()
        self.set_status("options", "")
        self.call_agent("slice", arguments, self.on_slice)

    def on_slice(self, response):
        self.slice_pending = False
        error = slicing.response_error(response)
        if error is not None:
            self.set_status("options", error)
            self.render_slice_button()
            return
        self.open_job(slicing.parse_job(response["result"]))

    # Job

    def open_job(self, job):
        self.job = job
        self.file_ready = False
        self.show_view("job", self.create_job_view())

    def create_job_view(self):
        self.labels["job_choice"] = wrapping_label()
        self.labels["job_state"] = wrapping_label()
        self.labels["job_message"] = wrapping_label(valign=Gtk.Align.START)
        view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, spacing=5)
        view.pack_start(self.labels["job_choice"], False, False, 0)
        view.pack_start(self.labels["job_state"], False, False, 0)
        view.pack_start(self.create_status("job"), False, False, 0)
        view.pack_start(self.create_scroll(self.labels["job_message"]), True, True, 0)
        view.pack_start(self.create_job_buttons(), False, False, 0)
        return view

    def create_job_buttons(self):
        self.labels["print"] = self._gtk.Button("printer", _("Print"), "color1", self.bts)
        self.labels["print"].connect("clicked", self.start_print)
        self.labels["slice_another"] = self._gtk.Button(
            "arrow-left", _("Slice another"), "color2", self.bts
        )
        self.labels["slice_another"].connect("clicked", self.slice_another)
        buttons = Gtk.Box(homogeneous=True, vexpand=False)
        for key in ("print", "slice_another"):
            self.labels[key].set_no_show_all(True)
            buttons.add(self.labels[key])
        return buttons

    def resume_job(self):
        self.render_job()
        self.call_agent("jobs", {"id": self.job.id}, self.on_job)
        self.check_file()

    def on_job(self, response):
        error = slicing.response_error(response)
        if error is not None:
            self.set_status("job", error)
            return
        self.update_job(slicing.parse_job(response["result"]))

    def process_update(self, action, data):
        if action != "notify_agent_event" or self.views[-1][0] != "job":
            return
        job = slicing.event_job(data, self.job.id)
        if job is not None:
            self.update_job(job)

    def update_job(self, job):
        self.job = slicing.advance(self.job, job)
        self.render_job()
        self.check_file()

    def render_job(self):
        self.labels["job_choice"].set_markup(bold_markup(slicing.job_choice_text(self.job)))
        self.labels["job_state"].set_text(slicing.job_status(self.job, self.file_ready))
        self.labels["job_message"].set_text(self.job.message)
        self.labels["print"].set_visible(slicing.can_print(self.job, self.file_ready))
        self.labels["slice_another"].set_visible(slicing.is_finished(self.job))

    def check_file(self):
        """Syncthing brings a finished gcode to the printer some seconds after the slice."""
        if self.checking_file or not slicing.waiting_for_file(self.job, self.file_ready):
            return
        self.checking_file = True
        self.request_metadata(self.token)

    def request_metadata(self, token):
        if token == self.token:
            self.poll_source = None
            self.send(
                "server.files.metadata", {"filename": self.job.printer_path}, self.on_metadata
            )
        return False

    def on_metadata(self, response):
        if slicing.response_error(response) is not None:
            self.poll_source = GLib.timeout_add_seconds(
                POLL_SECONDS, self.request_metadata, self.token
            )
            return
        self.checking_file = False
        self.file_ready = True
        self.render_job()

    def start_print(self, widget):
        logging.info(f"Starting print: {self.job.printer_path}")
        self._screen._ws.api.print_start(self.job.printer_path)

    def slice_another(self, widget):
        self.back()
