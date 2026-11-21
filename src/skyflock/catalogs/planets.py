"""Sun, Moon, and planets from a JPL kernel via Skyfield."""

from __future__ import annotations

from datetime import datetime, timezone

from skyflock.catalogs.base import MatchContext, SkyPrediction
from skyflock.geo import in_field
from skyflock.models.common import IdentityType, KnowledgeSource
from skyflock.store.db import Store

# de421 body names. Barycenters are the kernels that file actually contains.
_BODIES = (
    ("sun", "sun"),
    ("moon", "moon"),
    ("mercury", "mercury"),
    ("venus", "venus"),
    ("mars", "mars"),
    ("jupiter", "jupiter barycenter"),
    ("saturn", "saturn barycenter"),
    ("uranus", "uranus barycenter"),
    ("neptune", "neptune barycenter"),
)


class PlanetMatcher:
    name = "planet_ephemeris"
    priority = 20

    def __init__(self, loader=None, *, store: Store | None = None, ephemeris_path: str | None = None) -> None:
        self.loader = loader
        self.store = store
        self.ephemeris_path = ephemeris_path

    def predict(self, ctx: MatchContext) -> list[SkyPrediction]:
        if self.loader is not None:
            return self.loader(ctx)
        path = self.ephemeris_path or ctx.camera.ephemeris_path
        try:
            predictions = load_planet_predictions(ctx, path)
        except (ImportError, OSError):
            return []
        if self.store is not None and path:
            self.store.put_catalog_cache("de421", path)
        return predictions


def load_planet_predictions(ctx: MatchContext, ephemeris_path: str | None) -> list[SkyPrediction]:
    from skyfield.api import load, wgs84

    kernel = ephemeris_path or "de421.bsp"
    ephemeris = load(kernel)
    timescale = load.timescale()
    earth = ephemeris["earth"]
    site = ctx.camera.site
    observer = earth + wgs84.latlon(site.latitude_deg, site.longitude_deg, elevation_m=site.altitude_m)
    moment = timescale.from_datetime(datetime.fromtimestamp(ctx.time, tz=timezone.utc))
    predictions: list[SkyPrediction] = []
    for name, key in _BODIES:
        apparent = observer.at(moment).observe(ephemeris[key]).apparent()
        altitude, azimuth, _distance = apparent.altaz()
        if not in_field(azimuth.degrees % 360.0, altitude.degrees, ctx.camera):
            continue
        ra, dec, _distance = apparent.radec()
        predictions.append(
            SkyPrediction(
                identity_type=IdentityType.PLANET,
                knowledge_source=KnowledgeSource.PLANET_EPHEMERIS,
                catalog_id=name,
                name=name,
                azimuth_deg=azimuth.degrees % 360.0,
                elevation_deg=altitude.degrees,
                ra_deg=ra.hours * 15.0,
                dec_deg=dec.degrees,
            )
        )
    return predictions
