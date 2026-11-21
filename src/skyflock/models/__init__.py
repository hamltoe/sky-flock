"""Public data models. OpenSky columns stay intact; optical fields live beside them."""

from skyflock.models.aircraft import AircraftRecord, AircraftSnapshot
from skyflock.models.catalog import CatalogMatch
from skyflock.models.common import (
    IdentityType,
    Knowledge,
    KnowledgeSource,
    OpticalExtension,
    day_bucket,
    hour_bucket,
)
from skyflock.models.flight import Flight, TrackWaypoint
from skyflock.models.state_vector import StateVector

__all__ = [
    "AircraftRecord",
    "AircraftSnapshot",
    "CatalogMatch",
    "Flight",
    "IdentityType",
    "Knowledge",
    "KnowledgeSource",
    "OpticalExtension",
    "StateVector",
    "TrackWaypoint",
    "day_bucket",
    "hour_bucket",
]
