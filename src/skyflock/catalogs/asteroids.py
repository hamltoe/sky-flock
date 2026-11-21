"""Bright minor planets from an MPCORB extract. The full million-row file is not scanned blindly."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from skyflock.catalogs.base import MatchContext, SkyPrediction
from skyflock.catalogs.comets import _finite
from skyflock.geo import in_field
from skyflock.models.common import IdentityType, KnowledgeSource
from skyflock.store.db import Store

_SCAN_CAP = 5000


class AsteroidMatcher:
    name = "mpc_asteroid"
    priority = 40

    def __init__(self, loader=None, *, store: Store | None = None, elements_path: str | None = None) -> None:
        self.loader = loader
        self.store = store
        self.elements_path = elements_path

    def predict(self, ctx: MatchContext) -> list[SkyPrediction]:
        if self.loader is not None:
            return self.loader(ctx)
        path = self.elements_path or ctx.camera.mpcorb_path
        if not path:
            return []
        try:
            predictions = load_asteroid_predictions(ctx, path)
        except (ImportError, OSError, KeyError, AttributeError):
            return []
        if self.store is not None:
            self.store.put_catalog_cache("mpcorb", path)
        return predictions


def load_asteroid_predictions(ctx: MatchContext, elements_path: str) -> list[SkyPrediction]:
    from skyfield.api import load, wgs84
    from skyfield.constants import GM_SUN_DE440_km3_s2 as GM_SUN
    from skyfield.data import mpc

    with open(elements_path, "rb") as handle:
        minor_planets = mpc.load_mpcorb_dataframe(handle)
    if "semimajor_axis_au" in minor_planets.columns:
        minor_planets = minor_planets[minor_planets["semimajor_axis_au"].notna()]
    magnitude_column = next(
        (name for name in ("magnitude_H", "H", "magnitude") if name in minor_planets.columns),
        None,
    )
    if magnitude_column is not None:
        minor_planets = minor_planets[minor_planets[magnitude_column].notna()]
        minor_planets = minor_planets[minor_planets[magnitude_column] <= ctx.limit() + 10]
    kernel = ctx.camera.ephemeris_path or "de421.bsp"
    ephemeris = load(kernel)
    timescale = load.timescale()
    sun = ephemeris["sun"]
    earth = ephemeris["earth"]
    site = ctx.camera.site
    observer = earth + wgs84.latlon(site.latitude_deg, site.longitude_deg, elevation_m=site.altitude_m)
    moment = timescale.from_datetime(datetime.fromtimestamp(ctx.time, tz=timezone.utc))
    predictions: list[SkyPrediction] = []
    for scanned, (_index, row) in enumerate(minor_planets.iterrows()):
        if scanned >= _SCAN_CAP:
            break
        orbit = sun + mpc.mpcorb_orbit(row, timescale, GM_SUN)
        apparent = observer.at(moment).observe(orbit).apparent()
        altitude, azimuth, distance = apparent.altaz()
        if altitude.degrees < 0:
            continue
        heliocentric = sun.at(moment).observe(orbit).distance().au
        absolute = _finite(row.get(magnitude_column)) if magnitude_column else None
        magnitude = None
        if absolute is not None and distance.au > 0 and heliocentric > 0:
            magnitude = absolute + 5.0 * math.log10(distance.au * heliocentric)
        if magnitude is not None and magnitude > ctx.limit():
            continue
        if not in_field(azimuth.degrees % 360.0, altitude.degrees, ctx.camera):
            continue
        designation = str(row.get("designation") or row.name).strip()
        ra, dec, _distance = apparent.radec()
        predictions.append(
            SkyPrediction(
                identity_type=IdentityType.ASTEROID,
                knowledge_source=KnowledgeSource.MPC_ASTEROID,
                catalog_id=designation,
                name=designation,
                azimuth_deg=azimuth.degrees % 360.0,
                elevation_deg=altitude.degrees,
                ra_deg=ra.hours * 15.0,
                dec_deg=dec.degrees,
                magnitude=magnitude,
            )
        )
        if len(predictions) >= ctx.max_predictions:
            break
    return predictions
