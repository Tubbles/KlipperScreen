"""Pure orbit camera math for the model viewer.

Z is up, the projection is perspective, and matrices are row major numpy arrays.
"""

import math
from dataclasses import dataclass, replace

import numpy as np

FIELD_OF_VIEW = math.radians(45)
PITCH_LIMIT = math.radians(89)
WORLD_UP = np.array([0.0, 0.0, 1.0])
# Initial view from the front right and above, like a slicer.
INITIAL_YAW = math.atan2(-1.2, 1.5)
INITIAL_PITCH = math.radians(28)
FIT_MARGIN = 1.1


@dataclass(frozen=True)
class OrbitCamera:
    target: tuple
    distance: float
    yaw: float
    pitch: float
    model_radius: float
    scene_radius: float


def bounding_radius(bounds):
    return max(float(np.linalg.norm(bounds[1] - bounds[0])) / 2, 1.0)


def fit_distance(radius, aspect):
    """Distance at which a sphere of this radius fills the narrower field of view."""
    vertical = FIELD_OF_VIEW
    horizontal = 2 * math.atan(math.tan(vertical / 2) * aspect)
    return radius / math.sin(min(vertical, horizontal) / 2) * FIT_MARGIN


def fit_camera(model_bounds, scene_bounds, aspect):
    """Camera aimed at the centre of the model, far enough back to show all of it."""
    radius = bounding_radius(model_bounds)
    center = (model_bounds[0] + model_bounds[1]) / 2
    return OrbitCamera(
        target=tuple(float(value) for value in center),
        distance=fit_distance(radius, aspect),
        yaw=INITIAL_YAW,
        pitch=INITIAL_PITCH,
        model_radius=radius,
        scene_radius=bounding_radius(scene_bounds),
    )


def view_direction(camera):
    """Unit vector from the target towards the eye."""
    cos_pitch = math.cos(camera.pitch)
    return np.array(
        [cos_pitch * math.cos(camera.yaw), cos_pitch * math.sin(camera.yaw), math.sin(camera.pitch)]
    )


def eye_position(camera):
    return np.array(camera.target) + view_direction(camera) * camera.distance


def camera_axes(camera):
    """(right, up) unit vectors of the screen plane in world space."""
    forward = -view_direction(camera)
    right = np.cross(forward, WORLD_UP)
    right /= np.linalg.norm(right)
    return right, np.cross(right, forward)


def orbit(camera, delta_x, delta_y, viewport_height):
    """Rotate around the target. Dragging across the full view height turns half a revolution."""
    radians_per_pixel = math.pi / max(viewport_height, 1)
    pitch = camera.pitch + delta_y * radians_per_pixel
    return replace(
        camera,
        yaw=camera.yaw - delta_x * radians_per_pixel,
        pitch=min(max(pitch, -PITCH_LIMIT), PITCH_LIMIT),
    )


def units_per_pixel(camera, viewport_height):
    """World distance covered by one pixel at the depth of the target."""
    return 2 * camera.distance * math.tan(FIELD_OF_VIEW / 2) / max(viewport_height, 1)


def pan(camera, delta_x, delta_y, viewport_height):
    """Move the target so the point under the fingers follows them."""
    right, up = camera_axes(camera)
    scale = units_per_pixel(camera, viewport_height)
    target = np.array(camera.target) - right * delta_x * scale + up * delta_y * scale
    return replace(camera, target=tuple(float(value) for value in target))


def zoom(camera, scale):
    """Divide the distance by a pinch scale (above 1 moves closer), kept within sensible limits."""
    distance = camera.distance / max(scale, 1e-6)
    minimum = camera.model_radius * 0.05
    maximum = max(camera.scene_radius, camera.model_radius) * 20
    return replace(camera, distance=min(max(distance, minimum), maximum))


def look_at(eye, target, up):
    forward = target - eye
    forward = forward / np.linalg.norm(forward)
    right = np.cross(forward, up)
    right = right / np.linalg.norm(right)
    true_up = np.cross(right, forward)
    matrix = np.identity(4)
    matrix[0, :3], matrix[1, :3], matrix[2, :3] = right, true_up, -forward
    matrix[:3, 3] = -matrix[:3, :3] @ eye
    return matrix


def perspective(field_of_view, aspect, near, far):
    focal = 1 / math.tan(field_of_view / 2)
    matrix = np.zeros((4, 4))
    matrix[0, 0] = focal / aspect
    matrix[1, 1] = focal
    matrix[2, 2] = (far + near) / (near - far)
    matrix[2, 3] = 2 * far * near / (near - far)
    matrix[3, 2] = -1
    return matrix


def clip_planes(camera):
    """Near and far planes that keep the whole scene visible from any orbit position."""
    far = camera.distance + 2 * max(camera.scene_radius, camera.model_radius)
    return max(camera.distance * 0.01, far * 1e-5), far


def view_projection(camera, aspect):
    near, far = clip_planes(camera)
    view = look_at(eye_position(camera), np.array(camera.target), WORLD_UP)
    return perspective(FIELD_OF_VIEW, aspect, near, far) @ view
