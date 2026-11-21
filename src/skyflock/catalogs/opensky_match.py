"""Project OpenSky state vectors into the camera. This is the aircraft catalog."""

from __future__ import annotations

from skyflock.catalogs.base import MatchContext, SkyPrediction
from skyflock.geo import in_field, observer_azel
from skyflock.models.common import IdentityType, KnowledgeSource


class OpenSkyMatcher:
    name = "opensky"
    priority = 10

    def predict(self, ctx: MatchContext) -> list[SkyPrediction]:
        site = ctx.camera.site
        predictions: list[SkyPrediction] = []
        for vector in ctx.state_vectors:
            if vector.lat is None or vector.lon is None:
                continue
            altitude = vector.geoaltitude if vector.geoaltitude is not None else vector.baroaltitude
            if altitude is None or vector.icao24 is None:
                continue
            if not vector.is_fresh():
                continue
            stamp = vector.lastposupdate if vector.lastposupdate is not None else float(vector.time)
            if abs(ctx.time - stamp) > ctx.camera.time_gate_s:
                continue
            azimuth, elevation = observer_azel(
                site.latitude_deg,
                site.longitude_deg,
                site.altitude_m,
                vector.lat,
                vector.lon,
                altitude,
            )
            if not in_field(azimuth, elevation, ctx.camera, margin_deg=ctx.camera.opensky_gate_deg):
                continue
            predictions.append(
                SkyPrediction(
                    identity_type=IdentityType.ICAO,
                    knowledge_source=KnowledgeSource.OPENSKY,
                    catalog_id=vector.icao24,
                    name=vector.callsign,
                    azimuth_deg=azimuth,
                    elevation_deg=elevation,
                    state_vector=vector,
                )
            )
        return predictions
