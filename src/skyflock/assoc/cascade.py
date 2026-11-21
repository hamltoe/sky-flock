"""Try each catalog in order. The first accept wins; everything else stays unknown."""

from __future__ import annotations

import math
from dataclasses import dataclass

from skyflock.camera.config import CameraConfig
from skyflock.catalogs.base import MatchContext, SkyPrediction
from skyflock.catalogs.stars import HipparcosMatcher, StarHit, StarSolve
from skyflock.geo import angular_separation_deg, pixel_to_azel, radec_to_azel
from skyflock.models.common import IdentityType, Knowledge, KnowledgeSource, OpticalExtension, hour_bucket
from skyflock.models.state_vector import StateVector
from skyflock.store.db import Store


@dataclass
class Spot:
    spot_id: str
    time: int
    x: float
    y: float
    w: float = 1.0
    h: float = 1.0
    azimuth_deg: float | None = None
    elevation_deg: float | None = None
    size_px: float | None = None
    shape: str | None = None
    color: str | None = None


@dataclass
class Association:
    spot: Spot
    vector: StateVector
    row_id: int


def associate(
    spots: list[Spot],
    ctx: MatchContext,
    store: Store,
    matchers: list,
    stars: HipparcosMatcher | None = None,
) -> list[Association]:
    """Label every spot and append a state vector. Past rows are not rewritten."""
    stars = stars or HipparcosMatcher()
    solve = stars.solve(ctx)
    camera = _camera_from_solve(ctx.camera, solve, ctx.time)
    ctx = ctx.model_copy(update={"camera": camera})
    _apply_unknown_appearance(spots, solve)
    results: list[Association] = []
    remaining = list(spots)
    if solve.solver == "openstartracker":
        claimed, remaining = _claim_star_pixels(remaining, solve, camera)
        for spot, hit, residual in claimed:
            vector = _from_star_hit(spot, hit, camera, residual)
            results.append(_save(store, vector, spot))
    else:
        claimed_ids, star_results = _assign(
            remaining,
            stars.predict(ctx),
            camera,
            camera.angle_gate_deg,
            store,
        )
        results.extend(star_results)
        remaining = [spot for spot in remaining if id(spot) not in claimed_ids]
    for matcher in sorted(matchers, key=lambda item: item.priority):
        gate = camera.opensky_gate_deg if matcher.priority == 10 else camera.angle_gate_deg
        claimed_ids, matched = _assign(remaining, matcher.predict(ctx), camera, gate, store)
        results.extend(matched)
        remaining = [spot for spot in remaining if id(spot) not in claimed_ids]
    for spot in remaining:
        results.append(_save(store, _unknown(spot, camera), spot))
    return results


def _camera_from_solve(camera: CameraConfig, solve: StarSolve, unix_time: int) -> CameraConfig:
    if solve.ra_deg is None or solve.dec_deg is None:
        return camera
    azimuth, elevation = radec_to_azel(
        solve.ra_deg,
        solve.dec_deg,
        camera.site.latitude_deg,
        camera.site.longitude_deg,
        unix_time,
    )
    roll = solve.orientation_deg if solve.orientation_deg is not None else camera.mount.roll_deg
    mount = camera.mount.model_copy(update={"yaw_deg": azimuth, "pitch_deg": elevation, "roll_deg": roll})
    return camera.model_copy(update={"mount": mount})


def _apply_unknown_appearance(spots: list[Spot], solve: StarSolve) -> None:
    for blob in solve.unknowns:
        nearest = min(spots, key=lambda spot: math.hypot(spot.x - blob.x, spot.y - blob.y), default=None)
        if nearest is None or math.hypot(nearest.x - blob.x, nearest.y - blob.y) > 15:
            continue
        nearest.size_px = blob.size if blob.size is not None else nearest.size_px
        nearest.shape = blob.shape or nearest.shape
        nearest.color = blob.color or nearest.color


def _claim_star_pixels(
    spots: list[Spot],
    solve: StarSolve,
    camera: CameraConfig,
) -> tuple[list[tuple[Spot, StarHit, float]], list[Spot]]:
    used: set[int] = set()
    claimed: list[tuple[Spot, StarHit, float]] = []
    focal = camera.resolved_focal_px()
    for hit in solve.stars:
        if hit.x is None or hit.y is None:
            continue
        best: Spot | None = None
        best_distance = 12.0
        for spot in spots:
            if id(spot) in used:
                continue
            distance = math.hypot(spot.x - hit.x, spot.y - hit.y)
            if distance < best_distance:
                best = spot
                best_distance = distance
        if best is None:
            continue
        used.add(id(best))
        residual = math.degrees(math.atan2(best_distance, focal))
        claimed.append((best, hit, residual))
    return claimed, [spot for spot in spots if id(spot) not in used]


def _assign(
    spots: list[Spot],
    predictions: list[SkyPrediction],
    camera: CameraConfig,
    gate_deg: float,
    store: Store,
) -> tuple[set[int], list[Association]]:
    pairs: list[tuple[float, Spot, SkyPrediction]] = []
    for spot in spots:
        azimuth, elevation = _spot_azel(spot, camera)
        spot.azimuth_deg = azimuth
        spot.elevation_deg = elevation
        for prediction in predictions:
            separation = angular_separation_deg(
                azimuth, elevation, prediction.azimuth_deg, prediction.elevation_deg
            )
            if separation <= gate_deg:
                pairs.append((separation, spot, prediction))
    pairs.sort(key=lambda item: item[0])
    used_spots: set[int] = set()
    used_predictions: set[int] = set()
    saved: list[Association] = []
    for separation, spot, prediction in pairs:
        if id(spot) in used_spots or id(prediction) in used_predictions:
            continue
        used_spots.add(id(spot))
        used_predictions.add(id(prediction))
        saved.append(_save(store, _from_prediction(spot, prediction, camera, separation), spot))
    return used_spots, saved


def _spot_azel(spot: Spot, camera: CameraConfig) -> tuple[float, float]:
    if spot.azimuth_deg is not None and spot.elevation_deg is not None:
        return spot.azimuth_deg, spot.elevation_deg
    return pixel_to_azel(spot.x, spot.y, camera)


def _from_prediction(spot: Spot, prediction: SkyPrediction, camera: CameraConfig, residual: float) -> StateVector:
    if prediction.state_vector is not None and prediction.knowledge_source == KnowledgeSource.OPENSKY:
        vector = prediction.state_vector.model_copy(deep=True)
        vector.time = spot.time
        vector.hour = hour_bucket(spot.time)
    else:
        vector = StateVector(time=spot.time)
    vector.optical = _optical(spot, camera, residual, prediction=prediction)
    return vector


def _from_star_hit(spot: Spot, hit: StarHit, camera: CameraConfig, residual: float) -> StateVector:
    azimuth, elevation = _spot_azel(spot, camera)
    spot.azimuth_deg, spot.elevation_deg = azimuth, elevation
    prediction = SkyPrediction(
        identity_type=IdentityType.STAR,
        knowledge_source=KnowledgeSource.OPENSTARTRACKER,
        catalog_id=str(hit.hip),
        name=hit.name,
        ra_deg=hit.ra_deg,
        dec_deg=hit.dec_deg,
        magnitude=hit.mag,
        azimuth_deg=azimuth,
        elevation_deg=elevation,
    )
    return _from_prediction(spot, prediction, camera, residual)


def _unknown(spot: Spot, camera: CameraConfig) -> StateVector:
    azimuth, elevation = _spot_azel(spot, camera)
    spot.azimuth_deg, spot.elevation_deg = azimuth, elevation
    vector = StateVector(time=spot.time)
    vector.optical = OpticalExtension(
        camera_id=camera.camera_id,
        azimuth_deg=azimuth,
        elevation_deg=elevation,
        bbox=(spot.x, spot.y, spot.w, spot.h),
        identity_type=IdentityType.LOCAL,
        knowledge=Knowledge.UNKNOWN,
        local_id=spot.spot_id,
        size_px=spot.size_px if spot.size_px is not None else max(spot.w, spot.h),
        shape=spot.shape,
        color=spot.color,
        calibration_version=camera.calibration_version,
    )
    return vector


def _optical(
    spot: Spot,
    camera: CameraConfig,
    residual: float,
    *,
    prediction: SkyPrediction,
) -> OpticalExtension:
    azimuth, elevation = _spot_azel(spot, camera)
    return OpticalExtension(
        camera_id=camera.camera_id,
        azimuth_deg=azimuth,
        elevation_deg=elevation,
        bbox=(spot.x, spot.y, spot.w, spot.h),
        angular_residual_deg=residual,
        identity_type=prediction.identity_type,
        knowledge=Knowledge.KNOWN,
        knowledge_source=prediction.knowledge_source,
        local_id=spot.spot_id,
        catalog_id=prediction.catalog_id,
        ra_deg=prediction.ra_deg,
        dec_deg=prediction.dec_deg,
        magnitude=prediction.magnitude,
        size_px=spot.size_px if spot.size_px is not None else max(spot.w, spot.h),
        shape=spot.shape,
        color=spot.color,
        calibration_version=camera.calibration_version,
    )


def _save(store: Store, vector: StateVector, spot: Spot) -> Association:
    optical = vector.optical
    profile_id = None
    if optical is not None:
        profile_id = store.upsert_profile(
            identity_type=optical.identity_type,
            knowledge=optical.knowledge,
            knowledge_source=optical.knowledge_source,
            icao24=vector.icao24 if optical.identity_type == IdentityType.ICAO else None,
            catalog_id=None if optical.identity_type == IdentityType.ICAO else optical.catalog_id,
            local_id=optical.local_id if optical.identity_type == IdentityType.LOCAL else None,
            size_px=optical.size_px if optical.knowledge == Knowledge.UNKNOWN else None,
            shape=optical.shape if optical.knowledge == Knowledge.UNKNOWN else None,
            color=optical.color if optical.knowledge == Knowledge.UNKNOWN else None,
        )
    row_id = store.insert_state_vector(vector, profile_id=profile_id)
    return Association(spot=spot, vector=vector, row_id=row_id)
