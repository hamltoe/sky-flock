"""One optical or broadcast pass in the `flights_data4` shape."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator

from skyflock.models.common import day_bucket


class TrackWaypoint(BaseModel):
    time: int
    latitude: float | None = None
    longitude: float | None = None
    altitude: float | None = None
    heading: float | None = None
    onground: bool | None = None


class Flight(BaseModel):
    icao24: str | None = None
    firstseen: int | None = None
    estdepartureairport: str | None = None
    lastseen: int
    estarrivalairport: str | None = None
    callsign: str | None = None
    track: list[TrackWaypoint]
    estdepartureairporthorizdistance: int | None = None
    estdepartureairportvertdistance: int | None = None
    estarrivalairporthorizdistance: int | None = None
    estarrivalairportvertdistance: int | None = None
    departureairportcandidatescount: int | None = None
    arrivalairportcandidatescount: int | None = None
    day: int | None = None
    local_id: str | None = None
    catalog_id: str | None = None
    identity_type: str | None = None

    @field_validator("icao24")
    @classmethod
    def lower_icao(cls, value: str | None) -> str | None:
        if value is None or str(value).strip() == "":
            return None
        return str(value).strip().lower()

    def model_post_init(self, _context: Any) -> None:
        if self.day is None:
            object.__setattr__(self, "day", day_bucket(self.lastseen))

    def to_opensky_row(self) -> dict[str, Any]:
        return {
            "icao24": self.icao24,
            "firstseen": self.firstseen,
            "estdepartureairport": self.estdepartureairport,
            "lastseen": self.lastseen,
            "estarrivalairport": self.estarrivalairport,
            "callsign": self.callsign,
            "track": [point.model_dump() for point in self.track],
            "estdepartureairporthorizdistance": self.estdepartureairporthorizdistance,
            "estdepartureairportvertdistance": self.estdepartureairportvertdistance,
            "estarrivalairporthorizdistance": self.estarrivalairporthorizdistance,
            "estarrivalairportvertdistance": self.estarrivalairportvertdistance,
            "departureairportcandidatescount": self.departureairportcandidatescount,
            "arrivalairportcandidatescount": self.arrivalairportcandidatescount,
            "day": self.day,
        }
