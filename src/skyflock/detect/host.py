"""ONNX Runtime host. The installed wheel selects CPU, CUDA, or DirectML."""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path

import numpy as np

from skyflock.detect.threshold import ThresholdDetector
from skyflock.detect.types import Detection
from skyflock.detect.workload import WorkloadConfig

BUILTINS: dict[str, type] = {"threshold": ThresholdDetector}


def provider_list(device: str) -> list[str]:
    name = device.strip().lower()
    if name == "cpu":
        return ["CPUExecutionProvider"]
    if name == "cuda":
        return ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if name == "directml":
        return ["DmlExecutionProvider", "CPUExecutionProvider"]
    raise ValueError("device must be cpu, cuda, or directml")


class OnnxDetector:
    """Run one ONNX model. `onnxruntime`, `onnxruntime-gpu`, and `onnxruntime-directml` conflict."""

    def __init__(self, model_path: str | Path, workload: WorkloadConfig) -> None:
        import onnxruntime as ort

        self.model_path = str(model_path)
        self.workload = workload
        options = ort.SessionOptions()
        options.intra_op_num_threads = workload.threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            self.model_path,
            sess_options=options,
            providers=provider_list(workload.normalized_device()),
        )
        self.input_name = self.session.get_inputs()[0].name

    def detect(self, frame: np.ndarray) -> list[Detection]:
        array = np.asarray(frame)
        if array.ndim == 2:
            array = array[None, None, :, :]
        elif array.ndim == 3:
            array = array.transpose(2, 0, 1)[None, :, :, :]
        else:
            raise ValueError("frame must be a 2-D or 3-D array")
        batch = int(self.workload.batch_size)
        outputs = self.session.run(None, {self.input_name: array.astype(np.float32)})
        return _boxes_from_output(outputs[0], limit=batch)


def _boxes_from_output(output: np.ndarray, *, limit: int) -> list[Detection]:
    """Accept either Nx6 rows of x,y,w,h,score,class or a single detection vector."""
    array = np.asarray(output)
    if array.size == 0:
        return []
    rows = array.reshape(-1, array.shape[-1]) if array.ndim >= 2 else array.reshape(1, -1)
    detections: list[Detection] = []
    for row in rows[:limit]:
        if row.shape[0] < 5:
            continue
        detections.append(
            Detection(
                x=float(row[0]),
                y=float(row[1]),
                w=float(row[2]),
                h=float(row[3]),
                score=float(row[4]),
                class_label=str(int(row[5])) if row.shape[0] > 5 else "light",
            )
        )
    return detections


def load_detector(workload: WorkloadConfig):
    if workload.model_path:
        return OnnxDetector(workload.model_path, workload)
    name = workload.detector
    if name in BUILTINS:
        return BUILTINS[name](workload)
    matches = [item for item in entry_points(group="skyflock.detectors") if item.name == name]
    if not matches:
        raise KeyError(f"no detector named {name}")
    plugin = matches[0].load()
    return plugin(workload)
