"""Daytime mount fit from ADS-B control points projected into the plate."""

from __future__ import annotations

import numpy as np

from skyflock.camera.config import CameraConfig
from skyflock.geo import azel_to_pixel, wrap180, wrap360


def fit_pose(
    pairs: list[tuple[float, float, float, float]],
    camera: CameraConfig,
    *,
    fit_focal: bool = False,
    iterations: int = 25,
) -> CameraConfig:
    """Recover yaw, pitch, roll, and optionally focal length.

    Each pair is `(azimuth_deg, elevation_deg, pixel_x, pixel_y)`.
    """
    if len(pairs) < 3:
        raise ValueError("pose fitting needs at least three control points")
    yaw = camera.mount.yaw_deg
    pitch = camera.mount.pitch_deg
    roll = camera.mount.roll_deg
    focal = camera.resolved_focal_px()
    for _ in range(iterations):
        residual, jacobian = _system(pairs, camera, yaw, pitch, roll, focal, fit_focal)
        delta, *_ = np.linalg.lstsq(jacobian, -residual, rcond=None)
        if not np.all(np.isfinite(delta)):
            break
        yaw = wrap360(yaw + float(delta[0]))
        pitch = float(np.clip(pitch + float(delta[1]), -89.0, 89.0))
        roll = wrap180(roll + float(delta[2]))
        if fit_focal:
            focal = max(1.0, focal + float(delta[3]))
        if float(np.linalg.norm(delta)) < 1e-4:
            break
    mount = camera.mount.model_copy(update={"yaw_deg": yaw, "pitch_deg": pitch, "roll_deg": roll})
    return camera.model_copy(update={"mount": mount, "focal_px": focal})


def _system(
    pairs: list[tuple[float, float, float, float]],
    camera: CameraConfig,
    yaw: float,
    pitch: float,
    roll: float,
    focal: float,
    fit_focal: bool,
) -> tuple[np.ndarray, np.ndarray]:
    base = _project(pairs, camera, yaw, pitch, roll, focal)
    target = np.array([[u, v] for _az, _el, u, v in pairs], dtype=float).reshape(-1)
    residual = base - target
    steps = [1e-3, 1e-3, 1e-3] + ([0.5] if fit_focal else [])
    columns = []
    values = [yaw, pitch, roll] + ([focal] if fit_focal else [])
    for index, step in enumerate(steps):
        bumped = list(values)
        bumped[index] += step
        projected = _project(pairs, camera, *bumped[:3], bumped[3] if fit_focal else focal)
        columns.append((projected - base) / step)
    jacobian = np.column_stack(columns)
    return residual, jacobian


def _project(
    pairs: list[tuple[float, float, float, float]],
    camera: CameraConfig,
    yaw: float,
    pitch: float,
    roll: float,
    focal: float,
) -> np.ndarray:
    mount = camera.mount.model_copy(update={"yaw_deg": yaw, "pitch_deg": pitch, "roll_deg": roll})
    trial = camera.model_copy(update={"mount": mount, "focal_px": focal})
    points = []
    for az, el, _u, _v in pairs:
        pixel = azel_to_pixel(az, el, trial)
        if pixel is None:
            points.extend([1e6, 1e6])
        else:
            points.extend(pixel)
    return np.array(points, dtype=float)
