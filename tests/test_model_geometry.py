from array import array

import numpy as np
import pytest

from ks_includes import model_geometry as geometry


def binary_stl(triangles, header=b"binary"):
    records = np.zeros(len(triangles), dtype=geometry.STL_TRIANGLE_DTYPE)
    records["vertices"] = np.reshape(triangles, (-1, 3, 3))
    count = np.array([len(triangles)], dtype="<u4").tobytes()
    return header.ljust(80, b" ") + count + records.tobytes()


ONE_TRIANGLE = [[(0, 0, 5), (10, 0, 5), (0, 20, 5)]]

ASCII_STL = b"""solid part
  facet normal 0 0 1
    outer loop
      vertex 0 0 1
      vertex 2 0 1
      VERTEX 0 4 1
    endloop
  endfacet
endsolid part
"""


def gcode_toolpath(text):
    return geometry.parse_gcode(text.encode())


def segments_of(toolpath, first=0, count=None):
    """Start and end points of the strip segments drawn by the given index range."""
    indices = toolpath.indices[first : None if count is None else first + count]
    pairs = np.stack([indices[:-1], indices[1:]], axis=1)
    pairs = pairs[np.all(pairs != geometry.RESTART_INDEX, axis=1)]
    return toolpath.vertices[pairs].tolist()


def assert_segments(toolpath, expected):
    np.testing.assert_allclose(segments_of(toolpath), expected, atol=1e-6)


class TestStl:
    def test_binary_triangle_is_placed_on_bed_center_and_floor(self):
        mesh = geometry.parse_stl(binary_stl(ONE_TRIANGLE), bed_center=(100, 100))
        positions = mesh.vertices[:, :3]
        assert positions.tolist() == [[95, 90, 0], [105, 90, 0], [95, 110, 0]]
        assert mesh.bounds.tolist() == [[95, 90, 0], [105, 110, 0]]

    def test_normals_follow_winding_and_repeat_per_vertex(self):
        mesh = geometry.parse_stl(binary_stl(ONE_TRIANGLE))
        assert mesh.vertices[:, 3:].tolist() == [[0, 0, 1]] * 3

    def test_binary_file_with_solid_header_is_read_as_binary(self):
        mesh = geometry.parse_stl(binary_stl(ONE_TRIANGLE, header=b"solid looks like ascii"))
        assert len(mesh.vertices) == 3

    def test_ascii_file_with_mixed_case_keywords(self):
        mesh = geometry.parse_stl(ASCII_STL)
        assert mesh.vertices[:, :3].tolist() == [[-1, -2, 0], [1, -2, 0], [-1, 2, 0]]

    def test_ascii_file_with_non_ascii_name(self):
        mesh = geometry.parse_stl(ASCII_STL.replace(b"part", "Teil_\u00e4".encode()))
        assert len(mesh.vertices) == 3

    @pytest.mark.parametrize("header_count", [0, 7, 0xFFFFFFFF])
    def test_binary_count_comes_from_the_file_size(self, header_count):
        data = bytearray(binary_stl(ONE_TRIANGLE * 2))
        data[80:84] = np.array([header_count], dtype="<u4").tobytes()
        assert len(geometry.parse_stl(bytes(data)).vertices) == 6

    def test_binary_with_solid_header_and_wrong_count_is_read_as_binary(self):
        data = bytearray(binary_stl(ONE_TRIANGLE, header=b"solid looks like ascii"))
        data[80:84] = bytes(4)
        assert len(geometry.parse_stl(bytes(data)).vertices) == 3

    def test_binary_trailing_bytes_short_of_a_triangle_are_ignored(self):
        mesh = geometry.parse_stl(binary_stl(ONE_TRIANGLE) + b"\0" * 49)
        assert len(mesh.vertices) == 3

    @pytest.mark.parametrize("data", [b"", b"not a model", binary_stl([])])
    def test_file_without_triangles_raises(self, data):
        with pytest.raises(ValueError, match="no triangles"):
            geometry.parse_stl(data)

    def test_degenerate_triangle_gets_zero_normal(self):
        corners = np.array([[(0, 0, 0), (1, 1, 1), (2, 2, 2)]], dtype=np.float32)
        assert geometry.face_normals(corners).tolist() == [[0, 0, 0]]


class TestGcodeMoves:
    def test_travel_moves_are_not_drawn(self):
        toolpath = gcode_toolpath("G1 X10 Y10 Z0.2\nG1 X20 E1\nG0 X30\nG1 X40 E2\n")
        assert_segments(
            toolpath,
            [
                [[10, 10, 0.2], [20, 10, 0.2]],
                [[30, 10, 0.2], [40, 10, 0.2]],
            ],
        )

    def test_relative_extrusion(self):
        toolpath = gcode_toolpath("M83\nG1 Z0.2\nG1 X10 E0.5\nG1 X20 E0.5\nG1 X30 E-1\n")
        assert len(segments_of(toolpath)) == 2

    def test_absolute_extrusion_draws_only_increasing_e(self):
        toolpath = gcode_toolpath("M82\nG1 Z0.2\nG1 X10 E1\nG1 X20 E1\nG1 X30 E0.5\nG1 X40 E2\n")
        assert [segment[1][0] for segment in segments_of(toolpath)] == [10, 40]

    def test_g92_resets_extruder_position(self):
        toolpath = gcode_toolpath("M82\nG1 Z0.2\nG1 X10 E5\nG92 E0\nG1 X20 E1\n")
        assert [segment[1][0] for segment in segments_of(toolpath)] == [10, 20]

    def test_g92_without_axes_zeroes_every_axis(self):
        toolpath = gcode_toolpath("G1 X50 Y50 Z0.2 E5\nG92\nG1 X10 E1\n")
        np.testing.assert_allclose(segments_of(toolpath)[-1], [[0, 0, 0], [10, 0, 0]])

    def test_g92_sets_axis_positions(self):
        toolpath = gcode_toolpath("G1 Z0.2\nG92 X100 Y100\nG1 X110 E1\n")
        assert_segments(toolpath, [[[100, 100, 0.2], [110, 100, 0.2]]])

    def test_relative_positioning_also_makes_extrusion_relative(self):
        toolpath = gcode_toolpath(
            "M82\nG1 X10 Y10 Z0.2 E5\nG91\nG1 X5 E1\nG1 X5 E1\nG90\nG1 X0 E8\n"
        )
        assert [segment[1][0] for segment in segments_of(toolpath)] == [10, 15, 20, 0]

    def test_comments_case_and_unrelated_commands_are_ignored(self):
        text = (
            "; header X99 E99\ng1 z0.2 ; lower case\nG1 X10 E1 ; X50\n"
            "G10\nG28 X\nG11\nM104 S200\nG1 X20 E2\n"
        )
        assert [segment[1][0] for segment in segments_of(gcode_toolpath(text))] == [10, 20]

    def test_windows_line_endings(self):
        toolpath = gcode_toolpath("M83\r\nG1 Z0.2\r\nG1 X10 E1\r\nG1 X20 E1 ; done\r\n")
        assert [segment[1][0] for segment in segments_of(toolpath)] == [10, 20]

    def test_words_without_spaces_do_not_change_modes(self):
        toolpath = gcode_toolpath("M82\nG1 Z0.2\nG1 X10 E1\nG1X50E2\nG1 X20 E0.5\n")
        assert [segment[1][0] for segment in segments_of(toolpath)] == [10]

    def test_unparsable_word_keeps_previous_value(self):
        toolpath = gcode_toolpath("G1 Z0.2\nG1 X10 Yabc E1\n")
        assert_segments(toolpath, [[[0, 0, 0.2], [10, 0, 0.2]]])

    def test_arc_is_drawn_as_chord(self):
        toolpath = gcode_toolpath("G1 Z0.2\nG2 X10 Y10 I5 J0 E1\n")
        assert_segments(toolpath, [[[0, 0, 0.2], [10, 10, 0.2]]])

    def test_file_without_extrusion_raises(self):
        with pytest.raises(ValueError):
            gcode_toolpath("G28\nG1 X10 Y10\n")

    def test_cancel_stops_parsing(self):
        with pytest.raises(geometry.Cancelled):
            geometry.parse_gcode(b"G1 X1 E1\n", cancelled=lambda: True)


class TestLayers:
    TWO_LAYERS = "G1 Z0.2\nG1 X10 E1\nG1 X20 E1\nG1 Z0.6\nG1 X0\nG1 Z0.4\nG1 X10 E1\n"

    def test_layers_follow_extrusion_height_and_ignore_z_hops(self):
        toolpath = geometry.parse_gcode(("M83\n" + self.TWO_LAYERS).encode())
        assert toolpath.layer_ends.tolist() == [4, 6]
        assert toolpath.layer_heights.tolist() == pytest.approx([0.2, 0.4])

    def test_visible_range_of_top_layer(self):
        toolpath = geometry.parse_gcode(("M83\n" + self.TWO_LAYERS).encode())
        assert geometry.visible_index_range(toolpath, 1) == (0, 4)
        assert geometry.visible_index_range(toolpath, 2) == (4, 6)
        np.testing.assert_allclose(
            segments_of(toolpath, 0, 4),
            [[[0, 0, 0.2], [10, 0, 0.2]], [[10, 0, 0.2], [20, 0, 0.2]]],
            atol=1e-6,
        )
        np.testing.assert_allclose(
            segments_of(toolpath, 4, 2), [[[0, 0, 0.4], [10, 0, 0.4]]], atol=1e-6
        )

    def test_visible_range_clamps_layer_number(self):
        toolpath = geometry.parse_gcode(("M83\n" + self.TWO_LAYERS).encode())
        assert geometry.visible_index_range(toolpath, 0) == (0, 4)
        assert geometry.visible_index_range(toolpath, 99) == (4, 6)

    def test_heights_closer_than_resolution_share_a_layer(self):
        heights = np.array([0.2, 0.2001, 0.2002, 0.4])
        assert geometry.layer_starts(heights).tolist() == [0, 3]

    PRUSA_LAYERS = (
        "M83\nG1 Z0.3\nG1 X5 E1\nG1 X15 E1\n"
        ";LAYER_CHANGE\n;Z:0.2\nG1 Z0.2\nG1 X25 E1\nG1 X35 E1\n"
        ";BEFORE_LAYER_CHANGE\n;LAYER_CHANGE\n;Z:0.4\nG1 Z0.4\nG1 X45 E1\n"
    )

    def test_layer_comments_set_layers_and_a_purge_line_joins_the_first(self):
        toolpath = gcode_toolpath(self.PRUSA_LAYERS)
        assert len(toolpath.layer_ends) == 2
        assert toolpath.layer_heights.tolist() == pytest.approx([0.2, 0.4])

    def test_cura_layer_comments(self):
        text = (
            ";LAYER_COUNT:2\nM83\n;LAYER:0\nG1 Z0.2\nG1 X10 E1\n"
            ";LAYER:1\nG1 Z0.4\nG1 X20 E1\nG1 X30 E1\n"
        )
        toolpath = gcode_toolpath(text)
        assert toolpath.layer_ends.tolist() == [3, 6]
        assert toolpath.layer_heights.tolist() == pytest.approx([0.2, 0.4])

    def test_layer_comments_keep_a_spiral_in_one_layer(self):
        spiral = "".join(f"G1 X{index} Z{0.2 + index * 0.02:.2f} E1\n" for index in range(1, 10))
        toolpath = gcode_toolpath(f"M83\n;LAYER:0\nG1 Z0.2\n{spiral};LAYER:1\nG1 X0 Z0.4 E1\n")
        assert len(toolpath.layer_ends) == 2

    def test_comments_without_extrusions_add_no_layer(self):
        toolpath = gcode_toolpath(
            "M83\n;LAYER:0\n;LAYER:1\nG1 Z0.2\nG1 X10 E1\n;LAYER:2\nG1 Z0.4\n;LAYER:3\n"
            "G1 X20 E1\n;LAYER:4\nG1 Z10\n"
        )
        assert len(toolpath.layer_ends) == 2

    def test_only_comments_at_line_start_mark_layers(self):
        pieces = geometry.layer_pieces(
            b"G1 X1\n  ;LAYER:1\nG1 X2\n; before_layer_gcode = ;LAYER_CHANGE\\n\nG1 X3 ;LAYER:9\n"
        )
        assert pieces == [
            b"G1 X1\n",
            b"  ;LAYER:1\nG1 X2\n; before_layer_gcode = ;LAYER_CHANGE\\n\nG1 X3 ;LAYER:9\n",
        ]

    def test_comment_at_chunk_start_leaves_an_empty_first_piece(self):
        assert geometry.layer_pieces(b";LAYER_CHANGE\nG1 X1\n") == [b"", b";LAYER_CHANGE\nG1 X1\n"]


class TestStrips:
    def test_connected_moves_share_vertices(self):
        toolpath = gcode_toolpath("M83\nG1 Z0.2\nG1 X10 E1\nG1 Y10 E1\nG1 X0 E1\nG1 Y0 E1\n")
        assert len(toolpath.vertices) == 5
        assert toolpath.indices.tolist() == [0, 1, 2, 3, 4]

    def test_travel_starts_a_new_strip(self):
        toolpath = gcode_toolpath("M83\nG1 Z0.2\nG1 X10 E1\nG1 X20\nG1 X30 E1\n")
        assert toolpath.indices.tolist() == [0, 1, geometry.RESTART_INDEX, 2, 3]
        assert_segments(toolpath, [[[0, 0, 0.2], [10, 0, 0.2]], [[20, 0, 0.2], [30, 0, 0.2]]])

    def test_layer_change_starts_a_new_strip_even_without_travel(self):
        toolpath = gcode_toolpath("M83\nG1 Z0.2\nG1 X10 E1\nG1 X20 Z0.4 E1\n")
        assert toolpath.indices.tolist() == [0, 1, geometry.RESTART_INDEX, 2, 3]
        assert toolpath.layer_ends.tolist() == [3, 5]
        assert_segments(toolpath, [[[0, 0, 0.2], [10, 0, 0.2]], [[10, 0, 0.2], [20, 0, 0.4]]])


class TestBed:
    def test_bounds_from_toolhead_limits(self):
        assert geometry.bed_bounds([0, -5, -0.5, 0], [235, 230, 250, 0]) == ((0, -5), (235, 230))

    @pytest.mark.parametrize(
        "minimum, maximum", [({}, {}), (None, None), ([0, 0], [0, 100]), (["a", 0], [1, 1])]
    )
    def test_unusable_limits_give_none(self, minimum, maximum):
        assert geometry.bed_bounds(minimum, maximum) is None

    def test_center(self):
        assert geometry.bed_center(((0, 0), (235, 200))) == (117.5, 100)
        assert geometry.bed_center(None) == (0, 0)

    def test_lines_outline_the_bed_and_mark_the_origin(self):
        lines = geometry.bed_lines(((0, 0), (200, 100)))
        assert lines.shape == (14, 3)
        assert lines[:8, :2].min(axis=0).tolist() == [0, 0]
        assert lines[:8, :2].max(axis=0).tolist() == [200, 100]
        assert lines[13].tolist() == pytest.approx([0, 0, 8])

    def test_scene_bounds_contain_model_and_bed(self):
        model = np.array([[50.0, 50.0, 0.0], [300.0, 60.0, 40.0]])
        scene = geometry.scene_bounds(model, ((0, 0), (235, 235)))
        assert scene.tolist() == [[0, 0, 0], [300, 235, 40]]

    def test_bed_from_model_uses_xy_bounds(self):
        model = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
        assert geometry.bed_from_model(model) == ((1, 2), (4, 5))


class TestFocus:
    def test_mesh_focus_is_its_bounds(self):
        mesh = geometry.parse_stl(binary_stl(ONE_TRIANGLE))
        assert mesh.focus_bounds.tolist() == mesh.bounds.tolist()

    def test_toolpath_focus_ignores_a_purge_line(self):
        purge = "G1 X2 Y10 Z0.3\nG1 Y140 E10\n"
        part = "".join(
            f"G1 X{100 + index % 10} Y{100 + index // 10} E{11 + index}\n" for index in range(400)
        )
        toolpath = gcode_toolpath("G1 Z0.3\n" + purge + part)
        assert toolpath.bounds[0][0] == pytest.approx(2)
        assert toolpath.focus_bounds[0][0] > 99
        assert toolpath.focus_bounds[0][1] > 99


class TestChunks:
    def test_chunks_end_at_line_breaks_and_cover_everything(self):
        data = b"G1 X1\nG1 X22\nG1 X333\n"
        chunks = list(geometry.line_chunks(data, size=4))
        assert chunks == [b"G1 X1\n", b"G1 X22\n", b"G1 X333\n"]

    def test_last_chunk_without_line_break(self):
        assert list(geometry.line_chunks(b"G1 X1\nG1", size=100)) == [b"G1 X1\nG1"]

    def test_state_carries_across_chunks(self):
        state, moves = geometry.MotionState(), array("d")
        geometry.interpret_lines([b"M83", b"G91", b"G1 X5 Z0.2"], state, moves)
        geometry.interpret_lines([b"G1 X10 E1"], state, moves)
        assert list(moves[-4:]) == pytest.approx([15, 0, 0.2, 1])
