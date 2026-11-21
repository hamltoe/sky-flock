"""Score a recording against an hourly state-vector file."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from skyflock.assoc.cascade import Spot
from skyflock.camera.config import CameraConfig, load_camera
from skyflock.detect.workload import FpsGate, WorkloadConfig
from skyflock.geo import angular_separation_deg, in_field, observer_azel, pixel_to_azel
from skyflock.models.state_vector import StateVector
from skyflock.opensky.io import read_states


class ReplayReport(BaseModel):
    candidates: int
    matched: int
    association_rate: float
    mean_angular_error_deg: float | None
    max_angular_error_deg: float | None


def score_replay(
    spots: list[Spot],
    vectors: list[StateVector],
    camera: CameraConfig,
    *,
    gate_deg: float | None = None,
) -> ReplayReport:
    """Project broadcast vectors into the plate and match them to recorded spots."""
    gate = camera.opensky_gate_deg if gate_deg is None else gate_deg
    candidates: list[tuple[StateVector, float, float]] = []
    for vector in vectors:
        if vector.lat is None or vector.lon is None:
            continue
        altitude = vector.geoaltitude if vector.geoaltitude is not None else vector.baroaltitude
        if altitude is None:
            continue
        azimuth, elevation = observer_azel(
            camera.site.latitude_deg,
            camera.site.longitude_deg,
            camera.site.altitude_m,
            vector.lat,
            vector.lon,
            altitude,
        )
        if not in_field(azimuth, elevation, camera, margin_deg=gate):
            continue
        candidates.append((vector, azimuth, elevation))
    used: set[int] = set()
    errors: list[float] = []
    for _vector, azimuth, elevation in candidates:
        best: Spot | None = None
        best_error = gate
        for spot in spots:
            if id(spot) in used:
                continue
            spot_az, spot_el = _spot_angles(spot, camera)
            error = angular_separation_deg(spot_az, spot_el, azimuth, elevation)
            if error < best_error:
                best = spot
                best_error = error
        if best is None:
            continue
        used.add(id(best))
        errors.append(best_error)
    matched = len(errors)
    count = len(candidates)
    return ReplayReport(
        candidates=count,
        matched=matched,
        association_rate=(matched / count) if count else 0.0,
        mean_angular_error_deg=(sum(errors) / matched) if matched else None,
        max_angular_error_deg=max(errors) if errors else None,
    )


def load_spots(path: str | Path) -> list[Spot]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Spot.model_validate(item) if hasattr(Spot, "model_validate") else _spot_from_dict(item) for item in payload]


def _spot_from_dict(item: dict) -> Spot:
    return Spot(
        spot_id=str(item.get("spot_id") or item.get("id")),
        time=int(item["time"]),
        x=float(item["x"]),
        y=float(item["y"]),
        w=float(item.get("w", 1)),
        h=float(item.get("h", 1)),
        azimuth_deg=item.get("azimuth_deg"),
        elevation_deg=item.get("elevation_deg"),
    )


def replay_files(
    states_path: str | Path,
    detections_path: str | Path,
    camera_path: str | Path,
) -> ReplayReport:
    camera = load_camera(camera_path)
    return score_replay(load_spots(detections_path), read_states(states_path), camera)


def detections_from_video(
    path: str | Path,
    detector,
    camera: CameraConfig,
    workload: WorkloadConfig,
    *,
    start_time: int = 0,
) -> list[Spot]:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required to read a video; pass a detections JSON file instead") from exc
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"could not open video {path}")
    from skyflock.calib.mask import SkyMask

    gate = FpsGate(workload)
    mask = SkyMask.from_camera(camera)
    fps = capture.get(cv2.CAP_PROP_FPS) or 1.0
    spots: list[Spot] = []
    index = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            timestamp = start_time + (index / fps)
            index += 1
            if not gate.allow(timestamp):
                continue
            for detection in detector.detect(frame):
                if not mask.allows(detection.x, detection.y):
                    continue
                spots.append(
                    Spot(
                        spot_id=f"frame-{index}",
                        time=int(timestamp),
                        x=detection.x,
                        y=detection.y,
                        w=detection.w,
                        h=detection.h,
                    )
                )
    finally:
        capture.release()
    return spots


def _spot_angles(spot: Spot, camera: CameraConfig) -> tuple[float, float]:
    if spot.azimuth_deg is not None and spot.elevation_deg is not None:
        return spot.azimuth_deg, spot.elevation_deg
    return pixel_to_azel(spot.x, spot.y, camera)
