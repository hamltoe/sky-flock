"""Night-time plate calibration through astrometry.net's `solve-field` text."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from pydantic import BaseModel


class PlateSolution(BaseModel):
    ra_deg: float
    dec_deg: float
    orientation_deg: float | None = None
    width_arcmin: float | None = None
    height_arcmin: float | None = None


_CENTER = re.compile(
    r"Field center:\s*\(RA\s*,\s*Dec\)\s*=\s*\((?P<ra>[-+0-9.eE]+)\s*,\s*(?P<dec>[-+0-9.eE]+)\)",
    re.IGNORECASE,
)
_ROTATION = re.compile(
    r"Field rotation angle:\s*up is\s*(?P<angle>[-+0-9.eE]+)\s*degrees",
    re.IGNORECASE,
)
_SIZE = re.compile(
    r"Field size:\s*(?P<w>[-+0-9.eE]+)\s*x\s*(?P<h>[-+0-9.eE]+)\s*arcminutes",
    re.IGNORECASE,
)


def parse_solve_field_output(text: str) -> PlateSolution:
    center = _CENTER.search(text)
    if center is None:
        raise ValueError("solve-field output has no field center")
    rotation = _ROTATION.search(text)
    size = _SIZE.search(text)
    return PlateSolution(
        ra_deg=float(center.group("ra")),
        dec_deg=float(center.group("dec")),
        orientation_deg=float(rotation.group("angle")) if rotation else None,
        width_arcmin=float(size.group("w")) if size else None,
        height_arcmin=float(size.group("h")) if size else None,
    )


def solve_field(image: str | Path, *, binary: str = "solve-field", timeout_s: int = 120) -> PlateSolution:
    """Run astrometry.net. A score check against OpenStarTracker's ESA test stays with that tool."""
    completed = subprocess.run(
        [binary, str(image), "--no-plots", "--overwrite"],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode != 0 and "Field center" not in output:
        raise RuntimeError(output.strip() or f"{binary} failed with status {completed.returncode}")
    return parse_solve_field_output(output)
