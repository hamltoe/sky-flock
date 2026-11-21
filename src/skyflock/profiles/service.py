"""Aircraft snapshots, appearance notes, and promotions that leave past measurements alone."""

from __future__ import annotations

import csv
from pathlib import Path

from skyflock.models.aircraft import KEPT_COLUMNS, AircraftRecord
from skyflock.models.common import IdentityType, KnowledgeSource
from skyflock.store.db import Store


def import_aircraft_csv(store: Store, path: str | Path) -> int:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines:
        return store.add_aircraft_snapshot(str(file), [])
    quote = "'" if lines[0].lstrip().startswith("'") else '"'
    records: list[AircraftRecord] = []
    for raw in csv.DictReader(lines, quotechar=quote):
        normalized: dict[str, str | None] = {}
        for key, value in raw.items():
            if key is None:
                continue
            clean_key = str(key).strip().strip("'\"").lower()
            if isinstance(value, str):
                clean_value = value.strip().strip("'\"")
                normalized[clean_key] = clean_value or None
            else:
                normalized[clean_key] = value
        if not normalized.get("icao24"):
            continue
        kept = {column: normalized.get(column) for column in KEPT_COLUMNS}
        extra = {key: value for key, value in normalized.items() if key not in KEPT_COLUMNS}
        records.append(AircraftRecord(**kept, extra=extra))
    return store.add_aircraft_snapshot(str(file), records)


def add_revision(
    store: Store,
    profile_id: int,
    valid_from: int,
    *,
    class_label: str | None = None,
    notes: str | None = None,
    embedding_id: str | None = None,
    size_px: float | None = None,
    shape: str | None = None,
    color: str | None = None,
) -> int:
    return store.add_revision(
        profile_id,
        valid_from,
        class_label=class_label,
        notes=notes,
        embedding_id=embedding_id,
        size_px=size_px,
        shape=shape,
        color=color,
    )


def promote_unknown(
    store: Store,
    local_id: str,
    *,
    identity_type: IdentityType | str,
    at_time: int,
    catalog_id: str | None = None,
    icao24: str | None = None,
    knowledge_source: KnowledgeSource | str | None = None,
    note: str | None = None,
) -> int:
    """Attach a later catalog identity. Rows written before `at_time` keep their profile."""
    return store.promote(
        local_id,
        identity_type=identity_type,
        at_time=at_time,
        catalog_id=catalog_id,
        icao24=icao24.lower() if icao24 else None,
        knowledge_source=knowledge_source,
        note=note,
    )
