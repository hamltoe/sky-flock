"""OpenSky state vectors, in both the REST array shape and `state_vectors_data4`."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator

from skyflock.models.common import OpticalExtension, hour_bucket

REST_FIELDS = (
    "icao24",
    "callsign",
    "origin_country",
    "time_position",
    "last_contact",
    "longitude",
    "latitude",
    "baro_altitude",
    "on_ground",
    "velocity",
    "true_track",
    "vertical_rate",
    "sensors",
    "geo_altitude",
    "squawk",
    "spi",
    "position_source",
    "category",
)

HISTORICAL_FIELDS = (
    "time",
    "icao24",
    "lat",
    "lon",
    "velocity",
    "heading",
    "vertrate",
    "callsign",
    "onground",
    "alert",
    "spi",
    "squawk",
    "baroaltitude",
    "geoaltitude",
    "lastposupdate",
    "lastcontact",
    "serials",
    "hour",
)

HEADER_ALIASES = {
    "true_track": "heading",
    "vertical_rate": "vertrate",
    "baro_altitude": "baroaltitude",
    "geo_altitude": "geoaltitude",
    "on_ground": "onground",
    "time_position": "lastposupdate",
    "last_contact": "lastcontact",
    "longitude": "lon",
    "latitude": "lat",
    "sensors": "serials",
}


def _blank(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def _lower_icao(value: str | None) -> str | None:
    value = _blank(value)
    if value is None:
        return None
    return str(value).strip().lower()


class StateVector(BaseModel):
    """One `state_vectors_data4` row, plus the optional optical extension."""

    time: int
    icao24: str | None = None
    lat: float | None = None
    lon: float | None = None
    velocity: float | None = None
    heading: float | None = None
    vertrate: float | None = None
    callsign: str | None = None
    onground: bool | None = None
    alert: bool | None = None
    spi: bool | None = None
    squawk: str | None = None
    baroaltitude: float | None = None
    geoaltitude: float | None = None
    lastposupdate: float | None = None
    lastcontact: float | None = None
    serials: list[int] | None = None
    hour: int | None = None
    origin_country: str | None = None
    position_source: int | None = None
    category: int | None = None
    optical: OpticalExtension | None = None

    @field_validator("icao24")
    @classmethod
    def normalize_icao24(cls, value: str | None) -> str | None:
        return _lower_icao(value)

    @field_validator("callsign")
    @classmethod
    def strip_callsign(cls, value: str | None) -> str | None:
        value = _blank(value)
        if value is None:
            return None
        return str(value).strip()

    def model_post_init(self, _context: Any) -> None:
        if self.hour is None:
            object.__setattr__(self, "hour", hour_bucket(self.time))

    def is_fresh(self, max_age_s: float = 15) -> bool:
        """OpenSky keeps vectors for up to 300s; rows older than `max_age_s` since last contact are stale."""
        if self.lastcontact is None:
            return True
        return (self.time - self.lastcontact) <= max_age_s

    def historical_row(self) -> dict[str, Any]:
        row = self.model_dump()
        return {key: row[key] for key in HISTORICAL_FIELDS}

    def to_rest_array(self) -> list[Any]:
        return [
            self.icao24,
            self.callsign,
            self.origin_country,
            self.lastposupdate,
            self.lastcontact,
            self.lon,
            self.lat,
            self.baroaltitude,
            self.onground,
            self.velocity,
            self.heading,
            self.vertrate,
            self.serials,
            self.geoaltitude,
            self.squawk,
            self.spi,
            self.position_source,
            self.category,
        ]

    def to_rest_object(self) -> dict[str, Any]:
        values = self.to_rest_array()
        payload = dict(zip(REST_FIELDS, values, strict=True))
        payload["time"] = self.time
        payload["hour"] = self.hour
        payload["alert"] = self.alert
        if self.optical is not None:
            payload["optical"] = self.optical.model_dump(mode="json")
        return payload

    def came_from_opensky(self) -> bool:
        """Broadcast rows, including hourly files that have no position_source column."""
        from skyflock.models.common import KnowledgeSource

        if self.optical is not None:
            return self.optical.knowledge_source == KnowledgeSource.OPENSKY
        return self.icao24 is not None and self.position_source in (None, 0, 1, 2, 3)

    @classmethod
    def from_rest_array(cls, time: int, row: list[Any]) -> StateVector:
        if len(row) < 17:
            raise ValueError("an OpenSky state array needs at least 17 fields")
        padded = list(row) + [None] * (18 - len(row))
        return cls(
            time=int(time),
            icao24=_lower_icao(padded[0]),
            callsign=_blank(padded[1]),
            origin_country=_blank(padded[2]),
            lastposupdate=_float(padded[3]),
            lastcontact=_float(padded[4]),
            lon=_float(padded[5]),
            lat=_float(padded[6]),
            baroaltitude=_float(padded[7]),
            onground=_bool(padded[8]),
            velocity=_float(padded[9]),
            heading=_float(padded[10]),
            vertrate=_float(padded[11]),
            serials=_ints(padded[12]),
            geoaltitude=_float(padded[13]),
            squawk=_blank(padded[14]),
            spi=_bool(padded[15]),
            position_source=_int(padded[16]),
            category=_int(padded[17]) if len(row) >= 18 else None,
        )

    @classmethod
    def from_mapping(cls, raw: dict[str, Any], *, default_time: int | None = None) -> StateVector:
        data: dict[str, Any] = {}
        for key, value in raw.items():
            canonical = HEADER_ALIASES.get(str(key).strip().lower(), str(key).strip().lower())
            data[canonical] = value
        optical = data.pop("optical", None)
        if data.get("time") in (None, "") and default_time is not None:
            data["time"] = default_time
        if data.get("time") in (None, ""):
            raise ValueError("a state vector needs a time")
        vector = cls(
            time=int(float(data["time"])),
            icao24=_lower_icao(data.get("icao24")),
            lat=_float(data.get("lat")),
            lon=_float(data.get("lon")),
            velocity=_float(data.get("velocity")),
            heading=_float(data.get("heading")),
            vertrate=_float(data.get("vertrate")),
            callsign=_blank(data.get("callsign")),
            onground=_bool(data.get("onground")),
            alert=_bool(data.get("alert")),
            spi=_bool(data.get("spi")),
            squawk=_blank(data.get("squawk")),
            baroaltitude=_float(data.get("baroaltitude")),
            geoaltitude=_float(data.get("geoaltitude")),
            lastposupdate=_float(data.get("lastposupdate")),
            lastcontact=_float(data.get("lastcontact")),
            serials=_ints(data.get("serials")),
            hour=_int(data.get("hour")),
            origin_country=_blank(data.get("origin_country")),
            position_source=_int(data.get("position_source")),
            category=_int(data.get("category")),
        )
        if optical:
            if isinstance(optical, str):
                import json

                optical = json.loads(optical)
            vector.optical = OpticalExtension.model_validate(optical)
        return vector


def _float(value: Any) -> float | None:
    value = _blank(value)
    if value is None:
        return None
    return float(value)


def _int(value: Any) -> int | None:
    value = _blank(value)
    if value is None:
        return None
    return int(float(value))


def _bool(value: Any) -> bool | None:
    value = _blank(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes"}:
        return True
    if text in {"0", "false", "f", "no"}:
        return False
    raise ValueError(f"cannot read boolean from {value!r}")


def _ints(value: Any) -> list[int] | None:
    value = _blank(value)
    if value is None:
        return None
    if isinstance(value, list):
        return [int(item) for item in value]
    text = str(value).strip()
    if text.startswith("["):
        import json

        parsed = json.loads(text)
        return [int(item) for item in parsed]
    if not text:
        return None
    return [int(part) for part in text.replace(";", " ").split() if part]
