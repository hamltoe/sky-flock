"""Capture one frame: mask, detect, track, then run the catalog cascade."""

from __future__ import annotations

from skyflock.assoc.cascade import Association, Spot, associate
from skyflock.calib.mask import SkyMask
from skyflock.camera.config import CameraConfig
from skyflock.catalogs.base import MatchContext
from skyflock.catalogs.stars import HipparcosMatcher
from skyflock.detect.workload import FpsGate, WorkloadConfig
from skyflock.models.state_vector import StateVector
from skyflock.store.db import Store
from skyflock.track.tracker import Tracker


class Pipeline:
    def __init__(
        self,
        camera: CameraConfig,
        workload: WorkloadConfig,
        store: Store,
        detector,
        matchers: list,
        stars: HipparcosMatcher | None = None,
        tracker: Tracker | None = None,
    ) -> None:
        self.camera = camera
        self.workload = workload
        self.store = store
        self.detector = detector
        self.matchers = matchers
        self.stars = stars or HipparcosMatcher(
            csv_path=camera.hipparcos_csv,
            command=camera.openstartracker_command,
            store=store,
        )
        self.tracker = tracker or Tracker()
        self.mask = SkyMask.from_camera(camera)
        self.gate = FpsGate(workload)
        self.accepted = False

    def process_frame(
        self,
        frame,
        timestamp: float,
        state_vectors: list[StateVector] | None = None,
        image_path: str | None = None,
    ) -> list[Association]:
        if not self.camera.ready_to_track():
            self.accepted = False
            return []
        if not self.gate.allow(float(timestamp)):
            self.accepted = False
            return []
        self.accepted = True
        timestamp = int(timestamp)
        detections = [
            detection
            for detection in self.detector.detect(frame)
            if self.mask.allows(detection.x, detection.y)
        ]
        self.tracker.update(timestamp, detections)
        spots = [
            Spot(
                spot_id=tracklet.track_id,
                time=timestamp,
                x=tracklet.last.x,
                y=tracklet.last.y,
                w=tracklet.last.w,
                h=tracklet.last.h,
            )
            for tracklet in self.tracker.open
            if tracklet.last_time == timestamp
        ]
        ctx = MatchContext(
            time=timestamp,
            camera=self.camera,
            state_vectors=list(state_vectors or []),
            image_path=image_path,
        )
        results = associate(spots, ctx, self.store, self.matchers, self.stars)
        for flight in self.tracker.close_stale(timestamp):
            self.store.insert_flight(flight)
        return results
