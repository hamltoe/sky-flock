"""Import and export hourly state-vector files and REST JSON."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from skyflock.models.state_vector import HISTORICAL_FIELDS, StateVector
from skyflock.store.db import Store


def filter_fresh(vectors: list[StateVector], max_age_s: float = 15) -> list[StateVector]:
    return [vector for vector in vectors if vector.is_fresh(max_age_s)]


def read_states(path: str | Path, *, max_age_s: float = 15) -> list[StateVector]:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if file.suffix.lower() == ".json":
        vectors = _read_json(text)
    else:
        vectors = _read_csv(text)
    return filter_fresh(vectors, max_age_s)


def import_states(store: Store, path: str | Path, *, max_age_s: float = 15) -> int:
    vectors = read_states(path, max_age_s=max_age_s)
    for vector in vectors:
        store.insert_state_vector(vector)
    return len(vectors)


def export_hourly_csv(vectors: list[StateVector], path: str | Path) -> None:
    destination = Path(path)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(HISTORICAL_FIELDS))
        writer.writeheader()
        for vector in vectors:
            row = vector.historical_row()
            if row["serials"] is not None:
                row["serials"] = json.dumps(row["serials"])
            writer.writerow(row)


def export_rest_json(vectors: list[StateVector], path: str | Path, *, time: int | None = None) -> dict:
    payload = build_states_payload(vectors, time=time)
    Path(path).write_text(json.dumps(payload), encoding="utf-8")
    return payload


def build_states_payload(vectors: list[StateVector], *, time: int | None = None) -> dict:
    stamp = time if time is not None else (max((vector.time for vector in vectors), default=0))
    states = []
    for vector in vectors:
        if vector.came_from_opensky():
            states.append(vector.to_rest_array())
        else:
            states.append(vector.to_rest_object())
    return {"time": stamp, "states": states}


def _read_json(text: str) -> list[StateVector]:
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("{") or stripped.startswith("["):
        payload = json.loads(stripped)
        if isinstance(payload, dict) and "states" in payload:
            stamp = int(payload["time"])
            vectors = []
            for row in payload["states"] or []:
                if isinstance(row, list):
                    vectors.append(StateVector.from_rest_array(stamp, row))
                else:
                    vectors.append(StateVector.from_mapping(row, default_time=stamp))
            return vectors
        if isinstance(payload, list):
            return [StateVector.from_mapping(row) for row in payload]
    vectors = []
    for line in stripped.splitlines():
        if line.strip():
            vectors.append(StateVector.from_mapping(json.loads(line)))
    return vectors


def _read_csv(text: str) -> list[StateVector]:
    reader = csv.DictReader(text.splitlines())
    return [StateVector.from_mapping(row) for row in reader]
