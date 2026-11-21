"""CPU and GPU workload knobs for the detector host."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class WorkloadConfig(BaseModel):
    device: str = "cpu"
    threads: int = Field(default=4, ge=1)
    batch_size: int = Field(default=1, ge=1)
    max_fps: float = Field(default=10, gt=0)
    idle_fps: float = Field(default=1, gt=0, le=60)
    active_fps: float = Field(default=15, gt=0, le=60)
    hold_seconds: float = Field(default=4, ge=0, le=300)
    drop_frames: bool = True
    detector: str = "threshold"
    model_path: str | None = None
    threshold: float = 200
    cell: int = Field(default=8, ge=1)

    def normalized_device(self) -> str:
        device = self.device.strip().lower()
        if device not in {"cpu", "cuda", "directml"}:
            raise ValueError("device must be cpu, cuda, or directml")
        return device


class FpsGate:
    """Keep capture from outrunning the configured frame rate."""

    def __init__(self, workload: WorkloadConfig) -> None:
        self.workload = workload
        self._last: float | None = None

    def allow(self, timestamp: float) -> bool:
        if self._last is None:
            self._last = timestamp
            return True
        interval = 1.0 / self.workload.max_fps
        if timestamp - self._last < interval:
            return not self.workload.drop_frames
        self._last = timestamp
        return True


class CapturePace:
    """Stay on the slow rate until a spot appears, then hold the fast rate briefly."""

    def __init__(self) -> None:
        self.mode = "idle"
        self._last_spot: float | None = None

    def observe(self, spot_count: int, now: float, settings: WorkloadConfig) -> str:
        if spot_count > 0:
            self._last_spot = now
            self.mode = "active"
            return self.mode
        if self._last_spot is not None and now - self._last_spot <= settings.hold_seconds:
            self.mode = "active"
            return self.mode
        self._last_spot = None
        self.mode = "idle"
        return self.mode

    def fps(self, settings: WorkloadConfig) -> float:
        if self.mode == "active":
            return settings.active_fps
        return settings.idle_fps

    def interval(self, settings: WorkloadConfig) -> float:
        return 1.0 / self.fps(settings)


class WorkloadBox:
    """The live settings object the camera loop and the settings page share."""

    def __init__(self, config: WorkloadConfig, path: str | Path) -> None:
        self.config = config
        self.path = Path(path)

    def apply(self, config: WorkloadConfig) -> WorkloadConfig:
        for key, value in config.model_dump().items():
            setattr(self.config, key, value)
        dump_workload(self.config, self.path)
        return self.config


def load_workload(path: str | Path) -> WorkloadConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return WorkloadConfig.model_validate(raw)


def dump_workload(config: WorkloadConfig, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
