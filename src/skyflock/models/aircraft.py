"""Aircraft metadata snapshots. Imported rows are never edited in place."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator

KEPT_COLUMNS = (
    "icao24",
    "registration",
    "manufacturericao",
    "manufacturername",
    "model",
    "typecode",
    "serialnumber",
    "operator",
    "operatoricao",
    "owner",
    "built",
    "reguntil",
)


class AircraftRecord(BaseModel):
    icao24: str
    registration: str | None = None
    manufacturericao: str | None = None
    manufacturername: str | None = None
    model: str | None = None
    typecode: str | None = None
    serialnumber: str | None = None
    operator: str | None = None
    operatoricao: str | None = None
    owner: str | None = None
    built: str | None = None
    reguntil: str | None = None
    extra: dict[str, Any] = {}
    snapshot_id: int | None = None

    @field_validator("icao24")
    @classmethod
    def lower_icao(cls, value: str) -> str:
        text = str(value).strip().strip("'\"").lower()
        if not text:
            raise ValueError("icao24 is required")
        return text


class AircraftSnapshot(BaseModel):
    id: int | None = None
    source: str
    imported_at: int
    records: list[AircraftRecord] = []
