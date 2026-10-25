"""Pure parsing of STL and G-code files into numpy geometry for the model viewer.

Nothing here touches GTK or OpenGL, so it can be tested without a display.
"""

import re
from array import array
from dataclasses import dataclass

import numpy as np

STL_HEADER_SIZE = 84
STL_TRIANGLE_DTYPE = np.dtype(
    [
        ("normal", "<f4", (3,)),
        ("vertices", "<f4", (3, 3)),
        ("attribute", "<u2"),
    ]
)
STL_ASCII_VERTEX = re.compile(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", re.IGNORECASE)
# Binary STL headers may start with "solid" too, but binary triangle data nearly always holds zero
# bytes (attribute words, small coordinates), which ASCII STL never contains.
STL_ASCII_PROBE_BYTES = 1024

GCODE_COMMENT = re.compile(rb";[^\n]*")
# Lines that move the head or change how moves are interpreted.
# The lookahead keeps G1 from matching G10/G11/G17.
GCODE_MOTION_LINE = re.compile(rb"^[ \t]*(?:G0?[0-3]|G9[0-2]|M8[23])(?![0-9.])[^\n]*", re.MULTILINE)
GCODE_MOVE_COMMANDS = {b"G0", b"G1", b"G2", b"G3", b"G00", b"G01", b"G02", b"G03"}
# Layer change comments: ;LAYER_CHANGE from PrusaSlicer, SuperSlicer and OrcaSlicer, ;LAYER:<n>
# from Cura. ;LAYER_COUNT: and ;BEFORE_LAYER_CHANGE do not match. Without a ^ anchor the regex
# engine searches for the literal (0.03 s instead of 0.48 s for a 49 MB file on a laptop), so the
# line start is checked for each match instead.
GCODE_LAYER_MARKER = re.compile(rb";LAYER(?:_CHANGE|:)")
# Files are scanned in pieces of about this size. A regex call holds the GIL for its whole
# input, so pieces keep the GTK main loop responsive while a worker thread parses a large file.
CHUNK_BYTES = 1 << 20
# In files without layer change comments, extrusion heights closer than this belong to one layer,
# so vase mode spirals become slider steps of this size.
LAYER_HEIGHT_RESOLUTION = 0.01
# The camera frames the toolpath between these percentiles, so a purge line at the bed edge
# (a few segments among thousands) does not shrink the part to a speck.
FOCUS_PERCENTILES = (1, 99)
# Index that ends one line strip and starts the next (OpenGL primitive restart).
RESTART_INDEX = 0xFFFFFFFF

AXIS_LETTERS = {ord("X"): 0, ord("Y"): 1, ord("Z"): 2, ord("E"): 3}


@dataclass
class MotionState:
    """Modal state of the G-code interpreter carried from one chunk of the file to the next."""

    position: tuple = (0.0, 0.0, 0.0, 0.0)
    absolute: bool = True
    extrude_absolute: bool = True


@dataclass
class Mesh:
    """Triangles as interleaved position and face normal.

    6 float32 per vertex, 3 vertices per triangle.

    focus_bounds is the box the camera frames at first, the full bounds for a mesh.
    """

    vertices: np.ndarray
    bounds: np.ndarray
    focus_bounds: np.ndarray


@dataclass
class Toolpath:
    """Extrusion moves as line strips, stored layer after layer in print order.

    Consecutive moves share their joint vertex, which halves the vertices the GPU has to process
    compared with separate segments (measured 41 ms down to 27 ms per frame on a Raspberry Pi 4 for
    1.4 million segments). vertices holds float32 x y z, indices point into it with RESTART_INDEX
    between strips. Every layer starts a new strip, and layer_ends[i] is the number of indices in
    layers 0..i, so drawing layers 1..n means drawing the first layer_ends[n - 1] indices.
    layer_heights[i] is where the last extrusion of layer i ends.
    focus_bounds is the box the camera frames at first.
    """

    vertices: np.ndarray
    indices: np.ndarray
    layer_ends: np.ndarray
    layer_heights: np.ndarray
    bounds: np.ndarray
    focus_bounds: np.ndarray


class Cancelled(Exception):
    pass


def bounds_of(points):
    """Axis aligned bounds as a 2x3 array [minimum, maximum]."""
    return np.array([points.min(axis=0), points.max(axis=0)], dtype=np.float64)


def line_chunks(data, size=CHUNK_BYTES):
    """Pieces of data of at least size bytes (except the last) that end at a line break."""
    start = 0
    while start < len(data):
        end = data.find(b"\n", start + size)
        end = len(data) if end < 0 else end + 1
        yield data[start:end]
        start = end


# STL


def is_ascii_stl(data):
    head = data[:STL_ASCII_PROBE_BYTES]
    return head.lstrip()[:5].lower() == b"solid" and b"\0" not in head


def stl_triangle_count(data):
    """Triangles in binary STL data, from the file size.

    Some exporters write a wrong count into the header, so the header count is not used.
    """
    return max(len(data) - STL_HEADER_SIZE, 0) // STL_TRIANGLE_DTYPE.itemsize


def stl_binary_corners(data):
    count = stl_triangle_count(data)
    if count == 0:
        return np.empty((0, 3, 3), dtype=np.float32)
    triangles = np.frombuffer(data, dtype=STL_TRIANGLE_DTYPE, count=count, offset=STL_HEADER_SIZE)
    return triangles["vertices"].astype(np.float32)


def stl_ascii_corners(data):
    matches = [match for chunk in line_chunks(data) for match in STL_ASCII_VERTEX.findall(chunk)]
    numbers = np.array(matches).astype(np.float32)
    usable = len(numbers) - len(numbers) % 3
    return numbers[:usable].reshape(-1, 3, 3)


def stl_corners(data):
    """Triangle corners as a (triangles, 3, 3) float32 array from STL bytes.

    Anything that is not ASCII STL is read as binary.
    """
    if is_ascii_stl(data):
        return stl_ascii_corners(data)
    return stl_binary_corners(data)


def face_normals(corners):
    """Unit normals from the winding of each triangle. Degenerate triangles get a zero normal."""
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    return np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)


def placement_offset(bounds, bed_center):
    """Translation that puts the XY centre of the bounds on bed_center and the bottom on Z 0."""
    center = (bounds[0] + bounds[1]) / 2
    return np.array([bed_center[0] - center[0], bed_center[1] - center[1], -bounds[0][2]])


def interleave_mesh(corners, normals):
    vertices = np.empty((len(corners), 3, 6), dtype=np.float32)
    vertices[:, :, :3] = corners
    vertices[:, :, 3:] = normals[:, np.newaxis, :]
    return vertices.reshape(-1, 6)


def parse_stl(data, bed_center=(0.0, 0.0)):
    """Mesh placed on the bed the way a slicer places it."""
    corners = stl_corners(data)
    if len(corners) == 0:
        raise ValueError("STL file has no triangles")
    corners += placement_offset(bounds_of(corners.reshape(-1, 3)), bed_center).astype(np.float32)
    bounds = bounds_of(corners.reshape(-1, 3))
    return Mesh(interleave_mesh(corners, face_normals(corners)), bounds, bounds)


# G-code


def motion_lines(data):
    """Upper case G0-G3, G90-G92, M82 and M83 lines without comments."""
    return GCODE_MOTION_LINE.findall(GCODE_COMMENT.sub(b"", data).upper())


def word_value(word):
    try:
        return float(word[1:])
    except ValueError:
        return None


def set_position(words, position):
    """Position after G92. Without axis words every axis becomes zero, as in Klipper."""
    target = list(position)
    axis_words = [word for word in words[1:] if word[0] in AXIS_LETTERS]
    if not axis_words:
        return (0.0, 0.0, 0.0, 0.0)
    for word in axis_words:
        value = word_value(word)
        if value is not None:
            target[AXIS_LETTERS[word[0]]] = value
    return tuple(target)


def interpret_lines(lines, state, moves):
    """Append x, y, z and extruded length after every move and G92 to moves (an array of doubles).

    E is relative after G91 or M83, as in Klipper. Arcs (G2, G3) end at their end point, the arc
    itself is not traced. Words must be separated by whitespace, as slicers write them.

    This loop runs over a million times for a large file, so it keeps the whole move in local
    variables instead of helper calls (4.5 s down to 3.1 s for a 49 MB file on a laptop).
    """
    x, y, z, e = state.position
    absolute, extrude_absolute = state.absolute, state.extrude_absolute
    move_commands, extend = GCODE_MOVE_COMMANDS, moves.extend
    for line in lines:
        words = line.split()
        command = words[0]
        if command in move_commands:
            extrude_relative = not (absolute and extrude_absolute)
            start_e = e
            for word in words[1:]:
                letter = word[0]
                try:
                    if letter == 88:
                        x = float(word[1:]) if absolute else x + float(word[1:])
                    elif letter == 89:
                        y = float(word[1:]) if absolute else y + float(word[1:])
                    elif letter == 69:
                        e = e + float(word[1:]) if extrude_relative else float(word[1:])
                    elif letter == 90:
                        z = float(word[1:]) if absolute else z + float(word[1:])
                except ValueError:
                    pass
            extend((x, y, z, e - start_e))
        elif command == b"G92":
            x, y, z, e = set_position(words, (x, y, z, e))
            # A row that extrudes nothing, so the next segment starts from the new position.
            extend((x, y, z, 0.0))
        elif command in (b"G90", b"G91"):
            absolute = command == b"G90"
        elif command in (b"M82", b"M83"):
            extrude_absolute = command == b"M82"
    state.position = (x, y, z, e)
    state.absolute, state.extrude_absolute = absolute, extrude_absolute


def layer_marker_offsets(chunk):
    """Start of each line that begins with a layer change comment, after optional indentation."""
    offsets = []
    for match in GCODE_LAYER_MARKER.finditer(chunk):
        line_start = chunk.rfind(b"\n", 0, match.start()) + 1
        if not chunk[line_start : match.start()].strip(b" \t"):
            offsets.append(line_start)
    return offsets


def layer_pieces(chunk):
    """chunk cut in front of each layer change comment.

    Every piece after the first starts with one.
    """
    bounds = [0, *layer_marker_offsets(chunk), len(chunk)]
    return [chunk[start:end] for start, end in zip(bounds, bounds[1:])]


def interpret_gcode(data, cancelled=lambda: False):
    """Moves and layer change comments.

    Returns a (rows, 4) array of x, y, z and extruded length after each move or G92, from the
    origin, and the number of rows before each layer change comment.
    """
    state = MotionState()
    moves = array("d")
    marker_rows = []
    for chunk in line_chunks(data):
        if cancelled():
            raise Cancelled()
        first, *marked = layer_pieces(chunk)
        interpret_lines(motion_lines(first), state, moves)
        for piece in marked:
            marker_rows.append(len(moves) // 4)
            interpret_lines(motion_lines(piece), state, moves)
    rows = np.frombuffer(moves, dtype=np.float64).reshape(-1, 4)
    return rows, np.array(marker_rows, dtype=np.int64)


def extrusion_segments(moves):
    """(segments, 2, 3) start and end points of the rows that extrude while moving, and the rows."""
    ends = moves[:, :3]
    starts = np.vstack([np.zeros((1, 3)), ends[:-1]])
    rows = np.flatnonzero((moves[:, 3] > 0) & np.any(starts != ends, axis=1))
    return np.stack([starts[rows], ends[rows]], axis=1), rows


def layer_starts(heights):
    """First segment of each layer. A layer starts where the extrusion height changes."""
    keys = np.round(heights / LAYER_HEIGHT_RESOLUTION).astype(np.int64)
    return np.concatenate([[0], np.flatnonzero(np.diff(keys)) + 1])


def marker_layer_starts(segment_rows, marker_rows):
    """First segment of each layer, from the move rows of the layer change comments.

    Extrusions before the first comment (a purge line) join the first layer. A comment without
    extrusions after it adds no layer.
    """
    starts = np.searchsorted(segment_rows, marker_rows[1:])
    starts = np.unique(np.concatenate([[0], starts]))
    return starts[starts < len(segment_rows)]


def first_segments_of_layers(segments, segment_rows, marker_rows):
    """Layers follow the slicer's layer change comments, or the extrusion height without them."""
    if len(marker_rows):
        return marker_layer_starts(segment_rows, marker_rows)
    return layer_starts(segments[:, 1, 2])


def layer_heights(segments, first_segments):
    """Where the last extrusion of each layer ends.

    The first one may be a purge line or a seam at another height.
    """
    last_segments = np.append(first_segments[1:], len(segments)) - 1
    return segments[last_segments, 1, 2]


def strip_starts(segments, first_segments_of_layers):
    """True for each segment that begins a new strip.

    A segment begins a strip when it does not continue the previous one, or a layer starts.
    """
    starts = np.ones(len(segments), dtype=bool)
    starts[1:] = np.any(segments[1:, 0] != segments[:-1, 1], axis=1)
    starts[first_segments_of_layers] = True
    return starts


def strip_vertices(segments, starts):
    """Vertices of all strips in order.

    Each strip gives its first point, then the end point of each of its segments.
    """
    end_positions = np.arange(len(segments)) + np.cumsum(starts)
    vertices = np.empty((len(segments) + np.count_nonzero(starts), 3), dtype=np.float32)
    vertices[end_positions] = segments[:, 1]
    vertices[end_positions[starts] - 1] = segments[starts, 0]
    return vertices


def strip_indices(vertex_count, first_vertices_of_strips):
    """Vertex indices with RESTART_INDEX in front of every strip but the first."""
    indices = np.arange(vertex_count, dtype=np.uint32)
    return np.insert(indices, first_vertices_of_strips[1:], np.uint32(RESTART_INDEX))


def layer_index_ends(starts, first_segments_of_layers, index_count):
    """Index count at the end of each layer, from the position of each layer's first vertex."""
    strips_through = np.cumsum(starts)
    first_vertices = np.arange(len(starts)) + strips_through - 1
    first_indices = first_vertices + strips_through - 1
    return np.append(first_indices[first_segments_of_layers[1:]], index_count)


def toolpath_from_segments(segments, first_segments):
    starts = strip_starts(segments, first_segments)
    vertices = strip_vertices(segments, starts)
    first_vertices = np.flatnonzero(starts) + np.arange(np.count_nonzero(starts))
    indices = strip_indices(len(vertices), first_vertices)
    ends = layer_index_ends(starts, first_segments, len(indices))
    focus = np.percentile(vertices, FOCUS_PERCENTILES, axis=0)
    heights = layer_heights(segments, first_segments)
    return Toolpath(vertices, indices, ends, heights, bounds_of(vertices), focus)


def parse_gcode(data, cancelled=lambda: False):
    """Toolpath of the extrusion moves. cancelled is polled between chunks of the file."""
    moves, marker_rows = interpret_gcode(data, cancelled)
    segments, segment_rows = extrusion_segments(moves)
    if len(segments) == 0:
        raise ValueError("G-code file has no extrusion moves")
    first_segments = first_segments_of_layers(segments, segment_rows, marker_rows)
    return toolpath_from_segments(segments, first_segments)


def visible_index_range(toolpath, top_layer):
    """(first index of the top layer, indices up to and including it) for a 1-based top layer."""
    top_layer = min(max(int(top_layer), 1), len(toolpath.layer_ends))
    end = int(toolpath.layer_ends[top_layer - 1])
    start = int(toolpath.layer_ends[top_layer - 2]) if top_layer > 1 else 0
    return start, end


# Bed


def bed_bounds(axis_minimum, axis_maximum):
    """((x min, y min), (x max, y max)) from Klipper's toolhead axis limits, None if unusable."""
    try:
        low = (float(axis_minimum[0]), float(axis_minimum[1]))
        high = (float(axis_maximum[0]), float(axis_maximum[1]))
    except (TypeError, IndexError, KeyError, ValueError):
        return None
    return (low, high) if high[0] > low[0] and high[1] > low[1] else None


def bed_center(bed):
    return ((bed[0][0] + bed[1][0]) / 2, (bed[0][1] + bed[1][1]) / 2) if bed else (0.0, 0.0)


def bed_from_model(bounds):
    return (tuple(bounds[0][:2]), tuple(bounds[1][:2]))


def bed_lines(bed):
    """Line vertices: the bed outline (8 vertices), then X, Y and Z axis markers (2 each).

    The axis markers start at the origin corner.
    """
    (x0, y0), (x1, y1) = bed
    length = min(x1 - x0, y1 - y0) * 0.08
    return np.array(
        [
            (x0, y0, 0),
            (x1, y0, 0),
            (x1, y0, 0),
            (x1, y1, 0),
            (x1, y1, 0),
            (x0, y1, 0),
            (x0, y1, 0),
            (x0, y0, 0),
            (x0, y0, 0),
            (x0 + length, y0, 0),
            (x0, y0, 0),
            (x0, y0 + length, 0),
            (x0, y0, 0),
            (x0, y0, length),
        ],
        dtype=np.float32,
    )


def scene_bounds(model_bounds, bed):
    """Bounds that contain both the model and the bed outline."""
    (x0, y0), (x1, y1) = bed
    bed_box = np.array([(x0, y0, 0.0), (x1, y1, 0.0)])
    return np.array(
        [np.minimum(model_bounds[0], bed_box[0]), np.maximum(model_bounds[1], bed_box[1])]
    )
