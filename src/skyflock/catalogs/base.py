"""Catalog matchers share one prediction type and one context."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from skyflock.camera.config import CameraConfig
from skyflock.models.common import IdentityType, KnowledgeSource
from skyflock.models.state_vector import StateVector


class SkyPrediction(BaseModel):
    identity_type: IdentityType
    knowledge_source: KnowledgeSource
    catalog_id: str
    azimuth_deg: float
    elevation_deg: float
    name: str | None = None
    ra_deg: float | None = None
    dec_deg: float | None = None
    magnitude: float | None = None
    state_vector: StateVector | None = None

    model_config = {"arbitrary_types_allowed": True}


class MatchContext(BaseModel):
    time: int
    camera: CameraConfig
    state_vectors: list[StateVector] = []
    image_path: str | None = None
    magnitude_limit: float | None = None
    max_predictions: int = 200

    model_config = {"arbitrary_types_allowed": True}

    def limit(self) -> float:
        if self.magnitude_limit is not None:
            return self.magnitude_limit
        return self.camera.magnitude_limit


class CatalogMatcher(Protocol):
    name: str
    priority: int

    def predict(self, ctx: MatchContext) -> list[SkyPrediction]:
        """Return catalog bodies inside the camera field at `ctx.time`."""
