"""Shared enums and the optical extension stored beside every state vector."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class IdentityType(str, Enum):
    ICAO = "icao"
    STAR = "star"
    PLANET = "planet"
    COMET = "comet"
    ASTEROID = "asteroid"
    LOCAL = "local"


class Knowledge(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"


class KnowledgeSource(str, Enum):
    OPENSKY = "opensky"
    OPENSTARTRACKER = "openstartracker"
    PLANET_EPHEMERIS = "planet_ephemeris"
    MPC_COMET = "mpc_comet"
    MPC_ASTEROID = "mpc_asteroid"


def hour_bucket(unix_time: float) -> int:
    """Unix timestamp of the start of the UTC hour, matching OpenSky's `hour` partition."""
    whole = int(unix_time)
    return whole - (whole % 3600)


def day_bucket(unix_time: float) -> int:
    """Unix timestamp of 00:00 UTC on the day containing `unix_time`."""
    whole = int(unix_time)
    return whole - (whole % 86400)


class OpticalExtension(BaseModel):
    """Fields a camera adds. These never overwrite OpenSky columns."""

    camera_id: str | None = None
    azimuth_deg: float | None = None
    elevation_deg: float | None = None
    bbox: tuple[float, float, float, float] | None = None
    embedding_id: str | None = None
    angular_residual_deg: float | None = None
    identity_type: IdentityType
    knowledge: Knowledge
    knowledge_source: KnowledgeSource | None = None
    local_id: str | None = None
    catalog_id: str | None = None
    ra_deg: float | None = None
    dec_deg: float | None = None
    magnitude: float | None = None
    size_px: float | None = None
    shape: str | None = None
    color: str | None = None
    motion_class: str | None = None
    calibration_version: str | None = None
    class_label: str | None = None


def identity_key(
    identity_type: IdentityType | str,
    *,
    icao24: str | None = None,
    catalog_id: str | None = None,
    local_id: str | None = None,
) -> str:
    token = icao24 or catalog_id or local_id
    if not token:
        raise ValueError("a profile needs an icao24, catalog id, or local id")
    return f"{identity_type}:{token}"


class BBox(BaseModel):
    x: float
    y: float
    w: float = Field(ge=0)
    h: float = Field(ge=0)
