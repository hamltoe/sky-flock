"""Reference detector used when no ONNX model is configured."""

from __future__ import annotations

import numpy as np

from skyflock.detect.types import Detection
from skyflock.detect.workload import WorkloadConfig


class ThresholdDetector:
    def __init__(self, workload: WorkloadConfig | None = None) -> None:
        workload = workload or WorkloadConfig()
        self.threshold = workload.threshold
        self.cell = workload.cell
        self.devices = ("cpu",)

    def detect(self, frame: np.ndarray) -> list[Detection]:
        gray = _gray(frame)
        ys, xs = np.where(gray >= self.threshold)
        if len(xs) == 0:
            return []
        clusters: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
        for x, y in zip(xs.tolist(), ys.tolist(), strict=True):
            key = (int(x) // self.cell, int(y) // self.cell)
            clusters.setdefault(key, []).append((float(x), float(y), float(gray[y, x])))
        detections: list[Detection] = []
        for points in clusters.values():
            weight = sum(point[2] for point in points) or 1.0
            cx = sum(point[0] * point[2] for point in points) / weight
            cy = sum(point[1] * point[2] for point in points) / weight
            xs_ = [point[0] for point in points]
            ys_ = [point[1] for point in points]
            detections.append(
                Detection(
                    x=cx,
                    y=cy,
                    w=max(xs_) - min(xs_) + 1,
                    h=max(ys_) - min(ys_) + 1,
                    score=float(weight / len(points) / 255.0),
                )
            )
        return detections


def _gray(frame: np.ndarray) -> np.ndarray:
    array = np.asarray(frame)
    if array.ndim == 2:
        return array
    if array.ndim == 3:
        return array.max(axis=2)
    raise ValueError("frame must be a 2-D or 3-D array")
