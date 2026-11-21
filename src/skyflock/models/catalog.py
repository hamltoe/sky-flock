"""A hit from one catalog matcher. The catalog id is not an ICAO address."""

from __future__ import annotations

from pydantic import BaseModel

from skyflock.models.common import IdentityType, KnowledgeSource


class CatalogMatch(BaseModel):
    identity_type: IdentityType
    knowledge_source: KnowledgeSource
    catalog_id: str
    name: str | None = None
    ra_deg: float | None = None
    dec_deg: float | None = None
    magnitude: float | None = None
    azimuth_deg: float
    elevation_deg: float
    angular_residual_deg: float | None = None
