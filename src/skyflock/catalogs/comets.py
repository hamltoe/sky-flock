"""Comets from the Minor Planet Center CometEls.txt file, propagated with Skyfield."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from skyflock.catalogs.base import MatchContext, SkyPrediction
from skyflock.geo import in_field
from skyflock.models.common import IdentityType, KnowledgeSource
from skyflock.store.db import Store


class CometMatcher:
    name = "mpc_comet"
    priority = 30

    def __init__(self, loader=None, *, store: Store | None = None, elements_path: str | None = None) -> None:
        self.loader = loader
        self.store = store
        self.elements_path = elements_path

    def predict(self, ctx: MatchContext) -> list[SkyPrediction]:
        if self.loader is not None:
            return self.loader(ctx)
        path = self.elements_path or ctx.camera.comet_elements
        try:
            predictions = load_comet_predictions(ctx, path)
        except (ImportError, OSError, KeyError):
            return []
        if self.store is not None and path:
            self.store.put_catalog_cache("mpc_comets", path)
        return predictions


def load_comet_predictions(ctx: MatchContext, elements_path: str | None) -> list[SkyPrediction]:
    from skyfield.api import load, wgs84
    from skyfield.constants import GM_SUN_DE440_km3_s2 as GM_SUN
    from skyfield.data import mpc

    if elements_path:
        with open(elements_path, "rb") as handle:
            comets = mpc.load_comets_dataframe(handle)
    else:
        with load.open(mpc.COMET_URL) as handle:
            comets = mpc.load_comets_dataframe(handle)
    comets = comets.sort_values("reference").groupby("designation", as_index=False).last()
    kernel = ctx.camera.ephemeris_path or "de421.bsp"
    ephemeris = load(kernel)
    timescale = load.timescale()
    sun = ephemeris["sun"]
    earth = ephemeris["earth"]
    site = ctx.camera.site
    observer = earth + wgs84.latlon(site.latitude_deg, site.longitude_deg, elevation_m=site.altitude_m)
    moment = timescale.from_datetime(datetime.fromtimestamp(ctx.time, tz=timezone.utc))
    predictions: list[SkyPrediction] = []
    for _index, row in comets.iterrows():
        orbit = sun + mpc.comet_orbit(row, timescale, GM_SUN)
        apparent = observer.at(moment).observe(orbit).apparent()
        altitude, azimuth, distance = apparent.altaz()
        if altitude.degrees < 0:
            continue
        heliocentric = sun.at(moment).observe(orbit).distance().au
        magnitude = comet_magnitude(row, distance.au, heliocentric)
        if magnitude is not None and magnitude > ctx.limit():
            continue
        if not in_field(azimuth.degrees % 360.0, altitude.degrees, ctx.camera):
            continue
        designation = str(row["designation"]).strip()
        ra, dec, _distance = apparent.radec()
        predictions.append(
            SkyPrediction(
                identity_type=IdentityType.COMET,
                knowledge_source=KnowledgeSource.MPC_COMET,
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


def comet_magnitude(row, delta_au: float, r_au: float) -> float | None:
    """V = M1 + 5 log10(delta) + 2.5 k1 log10(r), with MPC columns magnitude_g and magnitude_k."""
    m1 = _finite(row.get("magnitude_g") if hasattr(row, "get") else None)
    if m1 is None or delta_au <= 0 or r_au <= 0:
        return None
    k1 = _finite(row.get("magnitude_k") if hasattr(row, "get") else None)
    if k1 is None:
        k1 = 0.0
    return m1 + 5.0 * math.log10(delta_au) + 2.5 * k1 * math.log10(r_au)


def _finite(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number
