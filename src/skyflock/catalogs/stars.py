"""Star identification.

OpenStarTracker (MIT, https://github.com/UBNanosatLab/openstartracker) is the
lost-in-space solver. It names Hipparcos stars and leaves every other spot
unmatched. When that program is not installed, a magnitude-limited Hipparcos
CSV is projected from the mount model instead.
"""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

from pydantic import BaseModel

from skyflock.catalogs.base import MatchContext, SkyPrediction
from skyflock.geo import in_field, radec_to_azel
from skyflock.models.common import IdentityType, KnowledgeSource
from skyflock.store.db import Store


class StarHit(BaseModel):
    hip: str
    x: float | None = None
    y: float | None = None
    ra_deg: float | None = None
    dec_deg: float | None = None
    mag: float | None = None
    name: str | None = None


class UnknownBlob(BaseModel):
    x: float
    y: float
    size: float | None = None
    shape: str | None = None
    color: str | None = None


class StarSolve(BaseModel):
    stars: list[StarHit] = []
    unknowns: list[UnknownBlob] = []
    ra_deg: float | None = None
    dec_deg: float | None = None
    orientation_deg: float | None = None
    solver: str = "hipparcos"


def parse_startracker_json(payload: dict) -> StarSolve:
    """Read the JSON contract printed by the OpenStarTracker adapter command."""
    stars = [StarHit.model_validate(item) for item in payload.get("stars") or []]
    unknowns = [UnknownBlob.model_validate(item) for item in payload.get("unknowns") or []]
    return StarSolve(
        stars=stars,
        unknowns=unknowns,
        ra_deg=payload.get("ra_deg"),
        dec_deg=payload.get("dec_deg"),
        orientation_deg=payload.get("orientation_deg"),
        solver="openstartracker",
    )


def run_openstartracker(command: list[str], image_path: str, timeout_s: int = 60) -> dict:
    completed = subprocess.run(
        [*command, image_path],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(detail or "openstartracker failed")
    return json.loads(completed.stdout)


def load_hipparcos_csv(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            ra = raw.get("ra_deg") or raw.get("ra")
            dec = raw.get("dec_deg") or raw.get("dec")
            hip = raw.get("hip") or raw.get("HIP")
            if ra is None or dec is None or not hip:
                continue
            mag_text = raw.get("mag") or raw.get("magnitude")
            rows.append(
                {
                    "hip": str(hip).strip(),
                    "ra_deg": float(ra),
                    "dec_deg": float(dec),
                    "mag": float(mag_text) if mag_text not in (None, "") else None,
                    "name": (raw.get("name") or "").strip() or None,
                }
            )
    return rows


class HipparcosMatcher:
    name = "openstartracker"
    priority = 0

    def __init__(
        self,
        csv_path: str | None = None,
        command: list[str] | None = None,
        *,
        runner=run_openstartracker,
        store: Store | None = None,
    ) -> None:
        self.csv_path = csv_path
        self.command = command
        self.runner = runner
        self.store = store

    def solve(self, ctx: MatchContext) -> StarSolve:
        command = self.command or ctx.camera.openstartracker_command
        if command and ctx.image_path:
            payload = self.runner(command, ctx.image_path)
            if self.store is not None:
                self.store.put_catalog_cache("openstartracker", ctx.image_path, meta={"solver": "openstartracker"})
            return parse_startracker_json(payload)
        return StarSolve(solver="hipparcos")

    def predict(self, ctx: MatchContext) -> list[SkyPrediction]:
        path = self.csv_path or ctx.camera.hipparcos_csv
        if not path:
            return []
        site = ctx.camera.site
        predictions: list[SkyPrediction] = []
        for row in load_hipparcos_csv(path):
            if row["mag"] is not None and row["mag"] > ctx.limit():
                continue
            azimuth, elevation = radec_to_azel(
                row["ra_deg"],
                row["dec_deg"],
                site.latitude_deg,
                site.longitude_deg,
                ctx.time,
            )
            if not in_field(azimuth, elevation, ctx.camera, margin_deg=ctx.camera.angle_gate_deg):
                continue
            predictions.append(
                SkyPrediction(
                    identity_type=IdentityType.STAR,
                    knowledge_source=KnowledgeSource.OPENSTARTRACKER,
                    catalog_id=str(row["hip"]),
                    name=row["name"],
                    ra_deg=row["ra_deg"],
                    dec_deg=row["dec_deg"],
                    magnitude=row["mag"],
                    azimuth_deg=azimuth,
                    elevation_deg=elevation,
                )
            )
        if self.store is not None:
            self.store.put_catalog_cache("hipparcos", path)
        return predictions
