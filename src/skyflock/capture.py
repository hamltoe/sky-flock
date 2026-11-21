"""Open a Windows camera, measure it, and run the tracker against live frames."""

from __future__ import annotations

import math
import threading
import time
from pathlib import Path

import yaml

from skyflock.calib.mask import SkyMask
from skyflock.camera.config import CameraConfig, Mount, Site, load_camera
from skyflock.catalogs.asteroids import AsteroidMatcher
from skyflock.catalogs.comets import CometMatcher
from skyflock.catalogs.opensky_match import OpenSkyMatcher
from skyflock.catalogs.planets import PlanetMatcher
from skyflock.detect.host import load_detector
from skyflock.detect.workload import CapturePace, WorkloadConfig, load_workload
from skyflock.pipeline import Pipeline
from skyflock.store.db import Store


class CameraDevice:
    def __init__(self, index: int, width: int, height: int) -> None:
        self.index = index
        self.width = width
        self.height = height


def probe_cameras(max_index: int = 4) -> list[CameraDevice]:
    """Try DirectShow indexes and keep the ones that return a frame."""
    cv2 = _cv2()
    found: list[CameraDevice] = []
    for index in range(max_index):
        capture = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        try:
            if not capture.isOpened():
                continue
            frame = _read_frame(capture)
            if frame is None:
                continue
            height, width = frame.shape[:2]
            found.append(CameraDevice(index=index, width=int(width), height=int(height)))
        finally:
            capture.release()
    return found


def webcam_config(device: CameraDevice, *, horizontal_fov_deg: float = 70.0) -> CameraConfig:
    """A starting plate for a built-in webcam. Aim and site coordinates come later."""
    focal_px = (device.width / 2.0) / math.tan(math.radians(horizontal_fov_deg / 2.0))
    return CameraConfig(
        camera_id=f"win-cam-{device.index}",
        source=device.index,
        width=device.width,
        height=device.height,
        lens_name="windows-webcam",
        zoom_fixed=True,
        focal_px=round(focal_px, 1),
        site=Site(latitude_deg=40.0, longitude_deg=-75.0, altitude_m=0.0),
        mount=Mount(yaw_deg=0.0, pitch_deg=45.0, roll_deg=0.0),
        calibration_version="windows-setup",
        sky_mask=[],
        magnitude_limit=6.5,
    )


def dump_camera(camera: CameraConfig, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = camera.model_dump(mode="json")
    destination.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def setup_camera(path: str | Path, *, max_index: int = 4) -> CameraConfig:
    devices = probe_cameras(max_index=max_index)
    if not devices:
        raise RuntimeError(
            "No camera returned a frame. Check Windows camera privacy, or connect a camera and try again."
        )
    camera = webcam_config(devices[0])
    dump_camera(camera, path)
    return camera


def watch(
    camera: CameraConfig,
    store: Store,
    preview_path: str | Path,
    stop: threading.Event,
    *,
    workload: WorkloadConfig | None = None,
    status: dict | None = None,
) -> None:
    """Read frames until `stop` is set. The newest annotated frame is written to `preview_path`."""
    cv2 = _cv2()
    workload = workload or WorkloadConfig(max_fps=2, drop_frames=True, threshold=200, cell=16)
    pipeline = Pipeline(
        camera,
        workload,
        store,
        load_detector(workload),
        [OpenSkyMatcher(), PlanetMatcher(), CometMatcher(), AsteroidMatcher()],
    )
    capture = cv2.VideoCapture(int(camera.source), cv2.CAP_DSHOW)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    destination = Path(preview_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pace = CapturePace()
    frames = 0
    try:
        if not capture.isOpened():
            raise RuntimeError(f"could not open camera {camera.source}")
        while not stop.is_set():
            started = time.perf_counter()
            frame = _read_frame(capture, attempts=2)
            if frame is None:
                stop.wait(0.05)
                continue
            frames += 1
            pipeline.camera = camera
            pipeline.mask = SkyMask.from_camera(camera)
            tracking = camera.ready_to_track()
            results = []
            if hasattr(pipeline.detector, "threshold"):
                pipeline.detector.threshold = workload.threshold
                pipeline.detector.cell = workload.cell
            if tracking:
                pipeline.workload.max_fps = max(workload.max_fps, workload.active_fps)
                pipeline.gate.workload = pipeline.workload
                results = pipeline.process_frame(frame, time.time(), [])
                _draw(cv2, frame, results)
                if pipeline.accepted:
                    pace.observe(len(results), time.monotonic(), workload)
            else:
                pace.observe(0, time.monotonic(), workload)
                cv2.putText(
                    frame,
                    "Tracking off until this camera is calibrated to the sky",
                    (12, 28),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (80, 220, 255),
                    1,
                )
            encoded = _encode_jpeg(cv2, frame)
            _write_preview(cv2, destination, frame)
            if status is not None:
                if encoded is not None:
                    status["preview_jpeg"] = encoded
                status["frames"] = frames
                status["tracking"] = tracking
                status["pace"] = pace.mode
                status["capture_fps"] = pace.fps(workload)
                status["error"] = None
                if not tracking or pipeline.accepted:
                    status["spots"] = len(results)
            remaining = pace.interval(workload) - (time.perf_counter() - started)
            if remaining > 0:
                stop.wait(remaining)
    except Exception as exc:
        if status is not None:
            status["error"] = str(exc)
        raise
    finally:
        capture.release()


def _draw(cv2, frame, results) -> None:
    for item in results:
        spot = item.spot
        optical = item.vector.optical
        label = optical.knowledge.value if optical is not None else "spot"
        if optical is not None and optical.catalog_id:
            label = f"{label}:{optical.catalog_id}"
        x = int(spot.x - spot.w / 2)
        y = int(spot.y - spot.h / 2)
        cv2.rectangle(frame, (x, y), (x + max(int(spot.w), 8), y + max(int(spot.h), 8)), (80, 220, 255), 2)
        cv2.putText(frame, label, (x, max(16, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 220, 255), 1)


def _encode_jpeg(cv2, frame) -> bytes | None:
    ok, encoded = cv2.imencode(".jpg", frame)
    if not ok:
        return None
    return encoded.tobytes()


def _write_preview(cv2, destination, frame) -> None:
    """Write a complete JPEG, then replace the preview so a reader never sees a partial file."""
    temporary = destination.with_name(destination.stem + ".tmp" + destination.suffix)
    if not cv2.imwrite(str(temporary), frame):
        return
    try:
        temporary.replace(destination)
    except OSError:
        return


def _read_frame(capture, attempts: int = 15):
    frame = None
    ok = False
    for _ in range(attempts):
        ok, frame = capture.read()
        if ok and frame is not None:
            return frame
    return None


def _cv2():
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV is required for the camera. Install it with: pip install opencv-python") from exc
    return cv2


def load_workload_or_default(path: str | Path | None) -> WorkloadConfig:
    if path is None:
        local = Path("config/workload.local.yaml")
        if local.is_file():
            return load_workload(local)
        return WorkloadConfig(idle_fps=1, active_fps=15, hold_seconds=4, max_fps=15, threshold=200, cell=16)
    return load_workload(path)


def open_configured_camera(path: str | Path) -> CameraConfig:
    return load_camera(path)
