import math

import numpy as np
import pytest

from ks_includes import orbit_camera

MODEL = np.array([[0.0, 0.0, 0.0], [40.0, 20.0, 10.0]])
SCENE = np.array([[0.0, 0.0, 0.0], [235.0, 235.0, 10.0]])
HEIGHT = 400
ASPECT = 1.5


def clip_coordinates(camera, point, aspect=ASPECT):
    return orbit_camera.view_projection(camera, aspect) @ np.append(point, 1.0)


def project(camera, point, aspect=ASPECT):
    """Normalised device coordinates of a world point."""
    clip = clip_coordinates(camera, point, aspect)
    return clip[:3] / clip[3]


def to_pixels(ndc):
    """Pixel offset from the view centre, y down like GTK events."""
    return np.array([ndc[0] * HEIGHT * ASPECT / 2, -ndc[1] * HEIGHT / 2])


def corners(bounds):
    return [np.array([x, y, z]) for x in bounds[:, 0] for y in bounds[:, 1] for z in bounds[:, 2]]


@pytest.fixture
def camera():
    return orbit_camera.fit_camera(MODEL, SCENE, ASPECT)


class TestFit:
    def test_target_is_model_center(self, camera):
        assert camera.target == (20, 10, 5)
        assert project(camera, np.array(camera.target))[:2] == pytest.approx([0, 0], abs=1e-9)

    @pytest.mark.parametrize("aspect", [0.5, 1.0, 1.6])
    def test_whole_model_is_on_screen(self, aspect):
        camera = orbit_camera.fit_camera(MODEL, SCENE, aspect)
        for corner in corners(MODEL):
            assert np.all(np.abs(project(camera, corner, aspect)[:2]) < 1)

    def test_scene_in_front_of_the_camera_stays_inside_clip_planes(self, camera):
        in_front = [corner for corner in corners(SCENE) if clip_coordinates(camera, corner)[3] > 0]
        assert in_front
        for corner in in_front:
            assert -1 < project(camera, corner)[2] < 1

    def test_tiny_model_gets_minimum_radius(self):
        point = np.array([[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]])
        assert orbit_camera.fit_camera(point, point, 1.0).model_radius == 1.0


class TestOrbit:
    def test_horizontal_drag_turns_yaw(self, camera):
        turned = orbit_camera.orbit(camera, HEIGHT / 2, 0, HEIGHT)
        assert turned.yaw == pytest.approx(camera.yaw - math.pi / 2)
        assert turned.pitch == camera.pitch

    def test_pitch_is_clamped(self, camera):
        assert orbit_camera.orbit(camera, 0, 10 * HEIGHT, HEIGHT).pitch == orbit_camera.PITCH_LIMIT
        assert (
            orbit_camera.orbit(camera, 0, -10 * HEIGHT, HEIGHT).pitch == -orbit_camera.PITCH_LIMIT
        )

    def test_orbit_keeps_target_centered(self, camera):
        turned = orbit_camera.orbit(camera, 123, -45, HEIGHT)
        assert project(turned, np.array(turned.target))[:2] == pytest.approx([0, 0], abs=1e-9)


class TestPan:
    def test_target_follows_the_fingers(self, camera):
        moved = orbit_camera.pan(camera, 30, -20, HEIGHT)
        offset = to_pixels(project(moved, np.array(camera.target)))
        assert offset == pytest.approx([30, -20], abs=1e-6)

    def test_pan_keeps_distance_and_angles(self, camera):
        moved = orbit_camera.pan(camera, 30, -20, HEIGHT)
        assert (moved.distance, moved.yaw, moved.pitch) == (
            camera.distance,
            camera.yaw,
            camera.pitch,
        )


class TestZoom:
    def test_pinch_out_moves_closer(self, camera):
        assert orbit_camera.zoom(camera, 2).distance == pytest.approx(camera.distance / 2)

    def test_distance_is_clamped(self, camera):
        assert orbit_camera.zoom(camera, 1e9).distance == pytest.approx(camera.model_radius * 0.05)
        assert orbit_camera.zoom(camera, 1e-9).distance == pytest.approx(camera.scene_radius * 20)


class TestMatrices:
    def test_look_at_maps_eye_to_origin_and_target_ahead(self):
        eye, target = np.array([10.0, -5.0, 3.0]), np.array([0.0, 0.0, 1.0])
        view = orbit_camera.look_at(eye, target, orbit_camera.WORLD_UP)
        assert (view @ np.append(eye, 1))[:3] == pytest.approx([0, 0, 0], abs=1e-9)
        ahead = view @ np.append(target, 1)
        assert ahead[:2] == pytest.approx([0, 0], abs=1e-9)
        assert ahead[2] == pytest.approx(-np.linalg.norm(target - eye))

    def test_perspective_maps_near_and_far_to_clip_range(self):
        matrix = orbit_camera.perspective(math.radians(45), 1.0, 1.0, 100.0)
        near = matrix @ np.array([0, 0, -1.0, 1])
        far = matrix @ np.array([0, 0, -100.0, 1])
        assert near[2] / near[3] == pytest.approx(-1)
        assert far[2] / far[3] == pytest.approx(1)
