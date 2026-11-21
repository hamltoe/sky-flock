"""Greedy tracklets. A tracklet that goes stale becomes one `flights_data4` row."""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

from skyflock.detect.types import Detection
from skyflock.models.flight import Flight, TrackWaypoint


@dataclass
class Tracklet:
    track_id: str
    points: list[tuple[int, Detection]] = field(default_factory=list)

    @property
    def last_time(self) -> int:
        return self.points[-1][0]

    @property
    def last(self) -> Detection:
        return self.points[-1][1]

    def to_flight(self) -> Flight:
        waypoints = [
            TrackWaypoint(time=stamp, onground=False)
            for stamp, _detection in self.points
        ]
        return Flight(
            firstseen=self.points[0][0],
            lastseen=self.points[-1][0],
            track=waypoints,
            local_id=self.track_id,
            identity_type="local",
        )


class Tracker:
    def __init__(self, *, gate_px: float = 40.0, max_gap_s: float = 3.0) -> None:
        self.gate_px = gate_px
        self.max_gap_s = max_gap_s
        self._ids = itertools.count(1)
        self.open: list[Tracklet] = []

    def update(self, timestamp: int, detections: list[Detection]) -> list[Tracklet]:
        unused = list(detections)
        for tracklet in self.open:
            if not unused:
                break
            nearest = min(unused, key=lambda item: _distance(tracklet.last, item))
            if _distance(tracklet.last, nearest) <= self.gate_px and (timestamp - tracklet.last_time) <= self.max_gap_s:
                tracklet.points.append((timestamp, nearest))
                unused.remove(nearest)
        for detection in unused:
            tracklet = Tracklet(track_id=f"track-{next(self._ids)}")
            tracklet.points.append((timestamp, detection))
            self.open.append(tracklet)
        return list(self.open)

    def close_stale(self, timestamp: int) -> list[Flight]:
        closed: list[Flight] = []
        still_open: list[Tracklet] = []
        for tracklet in self.open:
            if timestamp - tracklet.last_time > self.max_gap_s:
                closed.append(tracklet.to_flight())
            else:
                still_open.append(tracklet)
        self.open = still_open
        return closed


def _distance(left: Detection, right: Detection) -> float:
    return math.hypot(left.x - right.x, left.y - right.y)
