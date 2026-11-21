"""Per-camera lens, site, and mount settings."""

from __future__ import annotations

import math
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Site(BaseModel):
    latitude_deg: float
    longitude_deg: float
    altitude_m: float = 0.0


class Mount(BaseModel):
    yaw_deg: float
    pitch_deg: float
    roll_deg: float = 0.0


class CameraConfig(BaseModel):
    camera_id: str
    source: str | int = 0
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    pixel_size_um: float | None = None
    lens_name: str | None = None
    focal_length_mm: float | None = None
    zoom_fixed: bool = True
    distortion_k1: float = 0.0
    site: Site
    mount: Mount
    calibration_version: str = "1"
    sky_calibrated: bool = False
    sky_mask: list[tuple[float, float]] = []
    cx: float | None = None
    cy: float | None = None
    focal_px: float | None = None
    magnitude_limit: float = 6.5
    openstartracker_command: list[str] | None = None
    hipparcos_csv: str | None = None
    comet_elements: str | None = None
    mpcorb_path: str | None = None
    ephemeris_path: str | None = None
    angle_gate_deg: float = 0.2
    opensky_gate_deg: float = 1.0
    time_gate_s: float = 15.0

    def resolved_focal_px(self) -> float:
        if self.focal_px:
            return self.focal_px
        if self.focal_length_mm and self.pixel_size_um:
            return self.focal_length_mm / (self.pixel_size_um / 1000.0)
        raise ValueError("set focal_px, or both focal_length_mm and pixel_size_um")

    def principal_point(self) -> tuple[float, float]:
        cx = self.cx if self.cx is not None else self.width / 2.0
        cy = self.cy if self.cy is not None else self.height / 2.0
        return cx, cy

    def ready_to_track(self) -> bool:
        """Tracking starts only after a sky plate or ADS-B fit, plus a horizon mask."""
        return bool(self.sky_calibrated) and len(self.sky_mask) >= 3

    def fov_deg(self) -> float:
        focal = self.resolved_focal_px()
        half = max(self.width, self.height) / 2.0
        return math.degrees(2.0 * math.atan(half / focal))


def copy_config(destination: CameraConfig, source: CameraConfig) -> CameraConfig:
    """Copy fields onto the live camera so a running viewer sees the change."""
    for name in type(source).model_fields:
        value = getattr(source, name)
        current = getattr(destination, name)
        if isinstance(current, BaseModel) and isinstance(value, BaseModel):
            for sub in type(value).model_fields:
                setattr(current, sub, getattr(value, sub))
        else:
            setattr(destination, name, value)
    return destination


def load_camera(path: str | Path) -> CameraConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return CameraConfig.model_validate(raw)
