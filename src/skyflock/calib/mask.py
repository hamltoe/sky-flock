"""Horizon polygon. Detections outside it are dropped before profiling."""

from __future__ import annotations

from skyflock.camera.config import CameraConfig


class SkyMask:
    def __init__(self, polygon: list[tuple[float, float]]) -> None:
        self.polygon = [(float(x), float(y)) for x, y in polygon]

    @classmethod
    def from_camera(cls, camera: CameraConfig) -> SkyMask:
        return cls(camera.sky_mask)

    def allows(self, x: float, y: float) -> bool:
        """An empty polygon means the whole frame is treated as sky."""
        if len(self.polygon) < 3:
            return True
        return _point_in_polygon(x, y, self.polygon)


def _point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-15) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside
