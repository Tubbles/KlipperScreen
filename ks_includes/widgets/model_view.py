"""OpenGL view of an STL mesh or G-code toolpath that orbits, pans and zooms.

A one finger drag orbits, or pans while drag_pans is set, and zoom_by zooms in steps. The panel
drives both with on-screen buttons. Two fingers pan and pinch zoom only where GTK delivers touch
events. Raspberry Pi OS's GTK on X11 does not: the first finger arrives as the mouse and the
second is lost.

Needs an OpenGL 3.1 context (GLSL 1.40). GL objects live between the realize and unrealize
signals. The CPU copy of the model stays until clear(), so the view can re-upload it whenever
GTK realizes the widget again.
"""

import ctypes
import logging
from dataclasses import dataclass

import gi
import numpy as np
from OpenGL import GL
from OpenGL.GL import shaders

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GObject, Gtk

from ks_includes import model_geometry, orbit_camera

POSITION_LOCATION = 0
NORMAL_LOCATION = 1
BACKGROUND_COLOR = (0.12, 0.12, 0.13, 1.0)
MODEL_COLOR = (0.27, 0.73, 1.0)
BED_COLOR = (0.8, 0.8, 0.8)
AXIS_COLORS = ((1.0, 0.2, 0.2), (0.2, 0.93, 0.2), (0.33, 0.53, 1.0))
TOP_LAYER_COLOR = (1.0, 1.0, 1.0)

# Toplevel windows that GDK repaints whole, see repaint_whole_window_from_now_on.
WHOLE_WINDOW_REPAINT = []

MESH_VERTEX_SHADER = """#version 140
in vec3 position;
in vec3 normal;
uniform mat4 view_projection;
out vec3 surface_normal;
void main() {
    surface_normal = normal;
    gl_Position = view_projection * vec4(position, 1.0);
}
"""

# Two fixed directional lights plus ambient, like the slicer preview. Faces seen from behind are lit
# as if they faced the camera, so meshes with flipped triangles still look solid.
MESH_FRAGMENT_SHADER = """#version 140
in vec3 surface_normal;
uniform vec3 base_color;
out vec4 fragment_color;
void main() {
    vec3 normal = normalize(gl_FrontFacing ? surface_normal : -surface_normal);
    float key = max(dot(normal, vec3(0.485, 0.485, 0.728)), 0.0);
    float fill = max(dot(normal, vec3(-0.667, -0.333, -0.667)), 0.0);
    fragment_color = vec4(base_color * (0.35 + 0.65 * key + 0.25 * fill), 1.0);
}
"""

LINE_VERTEX_SHADER = """#version 140
in vec3 position;
uniform mat4 view_projection;
uniform vec2 height_range;
uniform bool color_by_height;
uniform vec3 line_color;
out vec3 vertex_color;
vec3 height_color(float height) {
    vec3 low = mix(vec3(0.2, 0.4, 1.0), vec3(0.1, 0.85, 0.9), clamp(height * 3.0, 0.0, 1.0));
    vec3 middle = mix(low, vec3(0.4, 0.9, 0.2), clamp(height * 3.0 - 1.0, 0.0, 1.0));
    return mix(middle, vec3(1.0, 0.6, 0.1), clamp(height * 3.0 - 2.0, 0.0, 1.0));
}
void main() {
    float span = max(height_range.y - height_range.x, 0.001);
    float height = (position.z - height_range.x) / span;
    vertex_color = color_by_height ? height_color(height) : line_color;
    gl_Position = view_projection * vec4(position, 1.0);
}
"""

LINE_FRAGMENT_SHADER = """#version 140
in vec3 vertex_color;
out vec4 fragment_color;
void main() {
    fragment_color = vec4(vertex_color, 1.0);
}
"""


@dataclass
class GpuBuffer:
    vertex_array: int
    buffer: int
    vertex_count: int
    index_buffer: int = 0


@dataclass
class Program:
    name: int
    uniforms: dict


def repaint_whole_window_on_paint(frame_clock, *arguments):
    """Emission hook on GdkFrameClock::paint. It runs before GDK repaints the window."""
    for gdk_window in WHOLE_WINDOW_REPAINT:
        if gdk_window.get_frame_clock() == frame_clock and gdk_window.get_update_area() is not None:
            gdk_window.invalidate_rect(None, True)
    return True


def repaint_whole_window_from_now_on(gdk_window):
    """Make GDK repaint all of gdk_window whenever it repaints a part of it.

    Once a GL area has been realized, GDK paints the toplevel through GL until the window is
    destroyed. On X11 it then repaints only the changed part when the back buffer's age says
    the rest is still in it, and from an age of 4 on it copies the changed part to the front
    buffer instead of swapping (gdk_x11_window_invalidate_for_new_frame). With Mesa V3D on a
    Raspberry Pi 4 the age stays at 4 and the rest of the window turns black, so the titlebar
    and action bar vanish. A whole window update always takes the repaint and swap path.
    """
    if not WHOLE_WINDOW_REPAINT:
        GObject.add_emission_hook(Gdk.FrameClock, "paint", repaint_whole_window_on_paint)
    if gdk_window not in WHOLE_WINDOW_REPAINT:
        WHOLE_WINDOW_REPAINT.append(gdk_window)


def build_program(vertex_source, fragment_source):
    stages = [
        shaders.compileShader(vertex_source, GL.GL_VERTEX_SHADER),
        shaders.compileShader(fragment_source, GL.GL_FRAGMENT_SHADER),
    ]
    program = GL.glCreateProgram()
    for stage in stages:
        GL.glAttachShader(program, stage)
    GL.glBindAttribLocation(program, POSITION_LOCATION, "position")
    GL.glBindAttribLocation(program, NORMAL_LOCATION, "normal")
    GL.glLinkProgram(program)
    for stage in stages:
        GL.glDeleteShader(stage)
    if GL.glGetProgramiv(program, GL.GL_LINK_STATUS) != GL.GL_TRUE:
        raise RuntimeError(GL.glGetProgramInfoLog(program))
    return program


def uniform_locations(program, names):
    return {name: GL.glGetUniformLocation(program, name) for name in names}


def build_programs():
    mesh = build_program(MESH_VERTEX_SHADER, MESH_FRAGMENT_SHADER)
    line = build_program(LINE_VERTEX_SHADER, LINE_FRAGMENT_SHADER)
    return {
        "mesh": Program(mesh, uniform_locations(mesh, ("view_projection", "base_color"))),
        "line": Program(
            line,
            uniform_locations(
                line, ("view_projection", "height_range", "color_by_height", "line_color")
            ),
        ),
    }


def upload_vertices(vertices, indices=None):
    """Vertex array for float32 rows of position, optionally followed by a normal.

    indices, when given, are uint32 and go into an element buffer.
    """
    vertex_array = GL.glGenVertexArrays(1)
    buffer = GL.glGenBuffers(1)
    GL.glBindVertexArray(vertex_array)
    index_buffer = 0 if indices is None else upload_indices(indices)
    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, buffer)
    GL.glBufferData(GL.GL_ARRAY_BUFFER, vertices.nbytes, vertices, GL.GL_STATIC_DRAW)
    stride = vertices.shape[1] * vertices.itemsize
    GL.glEnableVertexAttribArray(POSITION_LOCATION)
    GL.glVertexAttribPointer(
        POSITION_LOCATION, 3, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(0)
    )
    if vertices.shape[1] == 6:
        GL.glEnableVertexAttribArray(NORMAL_LOCATION)
        GL.glVertexAttribPointer(
            NORMAL_LOCATION, 3, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(12)
        )
    GL.glBindVertexArray(0)
    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
    return GpuBuffer(vertex_array, buffer, len(vertices), index_buffer)


def upload_indices(indices):
    """Element buffer, recorded in the vertex array that is bound."""
    index_buffer = GL.glGenBuffers(1)
    GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, index_buffer)
    GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, indices.nbytes, indices, GL.GL_STATIC_DRAW)
    return index_buffer


def delete_buffer(gpu_buffer):
    GL.glDeleteVertexArrays(1, [gpu_buffer.vertex_array])
    GL.glDeleteBuffers(1, [gpu_buffer.buffer])
    if gpu_buffer.index_buffer:
        GL.glDeleteBuffers(1, [gpu_buffer.index_buffer])


def gl_matrix(matrix):
    """Column major float32 copy of a row major matrix, for glUniformMatrix4fv without transpose."""
    return np.ascontiguousarray(matrix.T, dtype=np.float32)


def set_line_color(program, color):
    """A solid colour, or colouring by height when color is None."""
    GL.glUniform1i(program.uniforms["color_by_height"], int(color is None))
    if color is not None:
        GL.glUniform3f(program.uniforms["line_color"], *color)


def draw_lines(program, gpu_buffer, first, count, color):
    set_line_color(program, color)
    GL.glBindVertexArray(gpu_buffer.vertex_array)
    GL.glDrawArrays(GL.GL_LINES, first, count)


def draw_strips(program, gpu_buffer, first, count, color=None):
    """Indices first..first + count of indexed line strips separated by the restart index."""
    set_line_color(program, color)
    GL.glBindVertexArray(gpu_buffer.vertex_array)
    GL.glDrawElements(GL.GL_LINE_STRIP, count, GL.GL_UNSIGNED_INT, ctypes.c_void_p(first * 4))


class ModelView(Gtk.GLArea):
    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.set_has_depth_buffer(True)
        self.add_events(
            Gdk.EventMask.TOUCH_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON_MOTION_MASK
        )
        self.model = None
        self.bed = None
        self.camera = None
        self.top_layer = 0
        self.programs = None
        # Why the view cannot draw, for the panel to show. Set when the widget realizes.
        self.failure = None
        self.buffers = {}
        self.needs_upload = False
        self.drag_offset = (0.0, 0.0)
        # Set by the panel's pan toggle.
        self.drag_pans = False
        self.pinch_camera = None
        self.pinch_center = (0.0, 0.0)
        self.connect("realize", self.on_realize)
        self.connect("unrealize", self.on_unrealize)
        self.connect("render", self.on_render)
        self.connect("draw", self.on_draw)
        # GTK 3 gestures are not owned by the widget, so keep references.
        self.drag = Gtk.GestureDrag.new(self)
        self.drag.connect("drag-begin", self.on_drag_begin)
        self.drag.connect("drag-update", self.on_drag_update)
        self.pinch = Gtk.GestureZoom.new(self)
        self.pinch.connect("begin", self.on_pinch_begin)
        self.pinch.connect("update", self.on_pinch_update)

    def show_model(self, model, bed):
        """Show a Mesh or Toolpath.

        bed is ((x min, y min), (x max, y max)), or None to outline the model.
        """
        self.model = model
        self.bed = bed or model_geometry.bed_from_model(model.bounds)
        self.camera = None
        self.top_layer = len(model.layer_ends) if isinstance(model, model_geometry.Toolpath) else 0
        self.needs_upload = True
        self.queue_render()

    def clear(self):
        """Drop the model. Its GPU buffers go at the next render or when the widget unrealizes."""
        self.model = None
        self.camera = None
        self.needs_upload = True
        self.queue_render()

    def set_top_layer(self, layer):
        self.top_layer = layer
        self.queue_render()

    def zoom_by(self, scale):
        """Zoom like a pinch of this scale: above 1 moves closer."""
        if self.camera is None:
            return
        self.camera = orbit_camera.zoom(self.camera, scale)
        self.queue_render()

    def on_realize(self, area):
        repaint_whole_window_from_now_on(self.get_toplevel().get_window())
        self.failure = None
        self.make_current()
        if self.get_error() is not None:
            logging.error(f"No OpenGL context for the model view: {self.get_error()}")
            self.failure = f"{_('The model view needs OpenGL 3.1')}\n{self.get_error().message}"
            return
        try:
            self.programs = build_programs()
        except Exception as error:
            logging.exception(f"Unable to build the model view shaders: {error}")
            self.programs = None
            self.failure = _("The model view needs OpenGL 3.1")
        self.needs_upload = True

    def on_draw(self, area, cairo_context):
        """Skip GtkGLArea's own centred error text, which would overlap the panel's message."""
        return self.get_error() is not None

    def on_unrealize(self, area):
        self.make_current()
        if self.get_error() is None:
            self.delete_gpu_objects()
        self.programs = None
        self.buffers = {}
        self.needs_upload = True

    def delete_gpu_objects(self):
        for gpu_buffer in self.buffers.values():
            delete_buffer(gpu_buffer)
        for program in (self.programs or {}).values():
            GL.glDeleteProgram(program.name)

    def upload(self):
        for gpu_buffer in self.buffers.values():
            delete_buffer(gpu_buffer)
        self.buffers = {}
        if self.model is not None:
            indices = (
                self.model.indices if isinstance(self.model, model_geometry.Toolpath) else None
            )
            self.buffers["model"] = upload_vertices(self.model.vertices, indices)
            self.buffers["bed"] = upload_vertices(model_geometry.bed_lines(self.bed))
        self.needs_upload = False

    def viewport_size(self):
        return max(self.get_allocated_width(), 1), max(self.get_allocated_height(), 1)

    def current_camera(self):
        if self.camera is None:
            width, height = self.viewport_size()
            scene = model_geometry.scene_bounds(self.model.bounds, self.bed)
            self.camera = orbit_camera.fit_camera(self.model.focus_bounds, scene, width / height)
        return self.camera

    def on_render(self, area, context):
        GL.glClearColor(*BACKGROUND_COLOR)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        if self.programs is None:
            return True
        if self.needs_upload:
            self.upload()
        if self.model is None:
            return True
        width, height = self.viewport_size()
        matrix = gl_matrix(orbit_camera.view_projection(self.current_camera(), width / height))
        GL.glEnable(GL.GL_DEPTH_TEST)
        self.draw_bed(matrix)
        if isinstance(self.model, model_geometry.Toolpath):
            self.draw_toolpath(matrix)
        else:
            self.draw_mesh(matrix)
        GL.glBindVertexArray(0)
        return True

    def use_program(self, name, matrix):
        program = self.programs[name]
        GL.glUseProgram(program.name)
        GL.glUniformMatrix4fv(program.uniforms["view_projection"], 1, GL.GL_FALSE, matrix)
        return program

    def draw_bed(self, matrix):
        program = self.use_program("line", matrix)
        draw_lines(program, self.buffers["bed"], 0, 8, BED_COLOR)
        for axis, color in enumerate(AXIS_COLORS):
            draw_lines(program, self.buffers["bed"], 8 + axis * 2, 2, color)

    def draw_toolpath(self, matrix):
        """Layers below the top one coloured by height, the top layer highlighted."""
        program = self.use_program("line", matrix)
        GL.glUniform2f(
            program.uniforms["height_range"], self.model.bounds[0][2], self.model.bounds[1][2]
        )
        GL.glEnable(GL.GL_PRIMITIVE_RESTART)
        GL.glPrimitiveRestartIndex(model_geometry.RESTART_INDEX)
        start, end = model_geometry.visible_index_range(self.model, self.top_layer)
        draw_strips(program, self.buffers["model"], 0, start)
        draw_strips(program, self.buffers["model"], start, end - start, TOP_LAYER_COLOR)

    def draw_mesh(self, matrix):
        program = self.use_program("mesh", matrix)
        GL.glUniform3f(program.uniforms["base_color"], *MODEL_COLOR)
        GL.glBindVertexArray(self.buffers["model"].vertex_array)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, self.buffers["model"].vertex_count)

    def on_drag_begin(self, gesture, start_x, start_y):
        self.drag_offset = (0.0, 0.0)

    def on_drag_update(self, gesture, offset_x, offset_y):
        """One finger orbits, or pans while drag_pans is set."""
        if self.camera is None:
            return
        delta_x, delta_y = offset_x - self.drag_offset[0], offset_y - self.drag_offset[1]
        self.drag_offset = (offset_x, offset_y)
        move = orbit_camera.pan if self.drag_pans else orbit_camera.orbit
        self.camera = move(self.camera, delta_x, delta_y, self.viewport_size()[1])
        self.queue_render()

    def on_pinch_begin(self, gesture, sequence):
        """Claiming both touches cancels the one finger drag that the first touch started."""
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.pinch_camera = self.camera
        self.pinch_center = gesture.get_bounding_box_center()[1:]

    def on_pinch_update(self, gesture, sequence):
        """Two fingers pan with their centre and zoom with their spread, relative to the start."""
        if self.pinch_camera is None:
            return
        center = gesture.get_bounding_box_center()[1:]
        camera = orbit_camera.zoom(self.pinch_camera, gesture.get_scale_delta())
        delta_x, delta_y = center[0] - self.pinch_center[0], center[1] - self.pinch_center[1]
        self.camera = orbit_camera.pan(camera, delta_x, delta_y, self.viewport_size()[1])
        self.queue_render()
