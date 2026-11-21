import json
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import Request, urlopen

from skyflock.api.app import make_handler
from skyflock.assoc.cascade import Spot, associate
from skyflock.camera.config import CameraConfig, Mount, Site
from skyflock.catalogs.asteroids import AsteroidMatcher
from skyflock.catalogs.base import MatchContext, SkyPrediction
from skyflock.catalogs.comets import CometMatcher, comet_magnitude
from skyflock.catalogs.opensky_match import OpenSkyMatcher
from skyflock.catalogs.planets import PlanetMatcher
from skyflock.catalogs.stars import HipparcosMatcher
from skyflock.detect.workload import WorkloadBox, WorkloadConfig
from skyflock.geo import gmst_deg, observer_azel, radec_to_azel
from skyflock.models.common import IdentityType, Knowledge, KnowledgeSource, OpticalExtension
from skyflock.models.state_vector import StateVector
from skyflock.pipeline import Pipeline
from skyflock.profiles.service import add_revision, import_aircraft_csv, promote_unknown
from skyflock.replay import score_replay
from skyflock.store.db import Store


def rig_pointed_at(azimuth: float, elevation: float) -> CameraConfig:
    return CameraConfig(
        camera_id="cam",
        width=200,
        height=200,
        focal_px=800,
        site=Site(latitude_deg=0, longitude_deg=0, altitude_m=0),
        mount=Mount(yaw_deg=azimuth, pitch_deg=elevation, roll_deg=0),
        opensky_gate_deg=1.0,
        angle_gate_deg=0.3,
    )


def test_comet_magnitude_formula():
    class Row(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    assert comet_magnitude(Row(magnitude_g=5, magnitude_k=4), 1, 1) == 5


def test_opensky_wins_over_a_planet_at_the_same_spot():
    azimuth, elevation = observer_azel(0, 0, 0, 0, 0.05, 8000)
    camera = rig_pointed_at(azimuth, elevation)
    vector = StateVector(
        time=1_700_000_100,
        icao24="abc123",
        lat=0,
        lon=0.05,
        geoaltitude=8000,
        lastposupdate=1_700_000_100,
        lastcontact=1_700_000_100,
        position_source=0,
        callsign="TST123",
    )
    planet = SkyPrediction(
        identity_type=IdentityType.PLANET,
        knowledge_source=KnowledgeSource.PLANET_EPHEMERIS,
        catalog_id="venus",
        name="venus",
        azimuth_deg=azimuth,
        elevation_deg=elevation,
    )
    store = Store(":memory:")
    ctx = MatchContext(time=vector.time, camera=camera, state_vectors=[vector])
    spot = Spot(spot_id="track-1", time=vector.time, x=100, y=100, azimuth_deg=azimuth, elevation_deg=elevation)
    results = associate(
        [spot],
        ctx,
        store,
        [OpenSkyMatcher(), PlanetMatcher(loader=lambda _ctx: [planet]), CometMatcher(loader=lambda _ctx: []), AsteroidMatcher(loader=lambda _ctx: [])],
        HipparcosMatcher(),
    )
    assert len(results) == 1
    assert results[0].vector.icao24 == "abc123"
    assert results[0].vector.optical.knowledge_source == KnowledgeSource.OPENSKY
    assert results[0].vector.came_from_opensky()


def test_star_catalog_then_unknown(tmp_path):
    moment = 1_700_000_000
    ra = gmst_deg(moment)
    azimuth, elevation = radec_to_azel(ra, 0, 0, 0, moment)
    camera = rig_pointed_at(azimuth, elevation)
    catalog = tmp_path / "hip.csv"
    catalog.write_text("hip,ra_deg,dec_deg,mag,name\n32349,%.6f,0,-1.46,Sirius\n" % ra, encoding="utf-8")
    store = Store(":memory:")
    stars = HipparcosMatcher(csv_path=str(catalog), store=store)
    ctx = MatchContext(time=moment, camera=camera)
    star_spot = Spot(spot_id="star", time=moment, x=100, y=100, azimuth_deg=azimuth, elevation_deg=elevation)
    other = Spot(spot_id="ufo", time=moment, x=10, y=10, azimuth_deg=azimuth + 20, elevation_deg=elevation)
    results = associate([star_spot, other], ctx, store, [], stars)
    by_id = {item.spot.spot_id: item.vector for item in results}
    assert by_id["star"].optical.knowledge == Knowledge.KNOWN
    assert by_id["star"].optical.catalog_id == "32349"
    assert by_id["star"].icao24 is None
    assert by_id["ufo"].optical.knowledge == Knowledge.UNKNOWN
    assert by_id["ufo"].optical.identity_type == IdentityType.LOCAL
    assert store.get_catalog_cache("hipparcos")["path"] == str(catalog)


def test_openstartracker_json_claims_a_pixel():
    camera = rig_pointed_at(10, 30)

    def runner(_command, _image):
        return {
            "stars": [{"hip": "91262", "x": 40, "y": 50, "mag": 0.03, "name": "Vega"}],
            "unknowns": [{"x": 80, "y": 90, "size": 4, "shape": "round", "color": "white"}],
        }

    store = Store(":memory:")
    stars = HipparcosMatcher(command=["openstartracker"], runner=runner, store=store)
    ctx = MatchContext(time=50, camera=camera, image_path="frame.png")
    spots = [
        Spot(spot_id="a", time=50, x=40, y=50),
        Spot(spot_id="b", time=50, x=81, y=90),
    ]
    results = associate(spots, ctx, store, [], stars)
    by_id = {item.spot.spot_id: item.vector for item in results}
    assert by_id["a"].optical.identity_type == IdentityType.STAR
    assert by_id["a"].optical.catalog_id == "91262"
    assert by_id["b"].optical.knowledge == Knowledge.UNKNOWN
    assert by_id["b"].optical.shape == "round"
    assert by_id["b"].optical.color == "white"
    assert store.get_catalog_cache("openstartracker")["meta"]["solver"] == "openstartracker"


def test_promotion_keeps_past_measurements():
    store = Store(":memory:")
    local = "track-9"

    def write(when: int) -> int:
        vector = StateVector(
            time=when,
            optical=OpticalExtension(
                identity_type=IdentityType.LOCAL,
                knowledge=Knowledge.UNKNOWN,
                local_id=local,
                size_px=3,
                shape="round",
                color="white",
            ),
        )
        profile_id = store.upsert_profile(
            identity_type=IdentityType.LOCAL,
            knowledge=Knowledge.UNKNOWN,
            local_id=local,
            size_px=3,
            shape="round",
            color="white",
        )
        return store.insert_state_vector(vector, profile_id=profile_id)

    early = write(100)
    late = write(200)
    add_revision(store, store.state_vector_row(early)["profile_id"], 100, notes="first sighting")
    target = promote_unknown(
        store,
        local,
        identity_type=IdentityType.STAR,
        catalog_id="32349",
        at_time=150,
        knowledge_source=KnowledgeSource.OPENSTARTRACKER,
    )
    early_row = store.state_vector_row(early)
    late_row = store.state_vector_row(late)
    assert early_row["profile_id"] != target
    assert late_row["profile_id"] == target
    assert json.loads(early_row["optical"])["knowledge"] == "unknown"
    assert json.loads(late_row["optical"])["knowledge"] == "unknown"
    assert len(store.revisions_for(early_row["profile_id"])) == 1


def test_aircraft_snapshot_import(tmp_path):
    path = tmp_path / "aircraft.csv"
    path.write_text(
        "'icao24','registration','manufacturername','model','typecode','operator','operatoricao','owner','built','reguntil','notes'\n"
        "'AbC123','N12345','Cessna','172','C172','Private','','Owner','1999','','kept'\n",
        encoding="utf-8",
    )
    store = Store(":memory:")
    snapshot = import_aircraft_csv(store, path)
    record = store.get_aircraft("abc123")
    assert snapshot == record.snapshot_id
    assert record.registration == "N12345"
    assert record.typecode == "C172"
    assert record.extra["notes"] == "kept"


def test_replay_scores_a_projected_aircraft():
    azimuth, elevation = observer_azel(0, 0, 0, 0, 0.05, 8000)
    camera = rig_pointed_at(azimuth, elevation)
    vector = StateVector(
        time=10,
        icao24="abc123",
        lat=0,
        lon=0.05,
        geoaltitude=8000,
        lastcontact=10,
    )
    spots = [Spot(spot_id="seen", time=10, x=0, y=0, azimuth_deg=azimuth, elevation_deg=elevation)]
    report = score_replay(spots, [vector], camera)
    assert report.candidates == 1
    assert report.matched == 1
    assert report.association_rate == 1
    assert report.mean_angular_error_deg < 1e-6


def test_states_endpoint_serves_the_latest_sample():
    store = Store(":memory:")
    vector = StateVector(
        time=80,
        icao24="abc123",
        lat=10,
        lon=20,
        position_source=0,
        lastcontact=80,
    )
    store.insert_state_vector(vector)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with urlopen(f"http://127.0.0.1:{port}/states/all?icao24=abc123") as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["time"] == 80
        assert payload["states"][0][0] == "abc123"
    finally:
        server.shutdown()
        server.server_close()


def test_settings_page_saves_the_live_capture_rate(tmp_path):
    config = WorkloadConfig()
    box = WorkloadBox(config, tmp_path / "workload.local.yaml")
    status = {"workload_box": box, "pace": "idle", "capture_fps": 1}
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Store(":memory:"), status=status))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        body = "idle_fps=0.5&active_fps=12&hold_seconds=6&threshold=180&cell=12".encode()
        request = Request(f"http://127.0.0.1:{port}/settings", data=body, method="POST")
        with urlopen(request) as response:
            page = response.read().decode("utf-8")
        assert response.status == 200
        assert "Capture settings saved" in page
        assert box.config.idle_fps == 0.5
        assert box.config.active_fps == 12
        assert box.config.max_fps == 12
        assert box.config.hold_seconds == 6
        assert box.config.threshold == 180
        assert box.config.cell == 12
        assert "idle_fps: 0.5" in (tmp_path / "workload.local.yaml").read_text(encoding="utf-8")
    finally:
        server.shutdown()
        server.server_close()


def test_uncalibrated_camera_stores_nothing():
    import numpy as np

    from skyflock.detect.host import load_detector

    camera = rig_pointed_at(45, 30)
    store = Store(":memory:")
    pipeline = Pipeline(
        camera,
        WorkloadConfig(max_fps=100, drop_frames=False, threshold=200, cell=4),
        store,
        load_detector(WorkloadConfig(threshold=200, cell=4)),
        [],
    )
    frame = np.zeros((40, 40), dtype=np.uint8)
    frame[4:8, 4:8] = 255
    assert pipeline.process_frame(frame, 10, []) == []
    assert store.conn.execute("SELECT COUNT(*) FROM state_vectors").fetchone()[0] == 0


def test_flush_sightings_keeps_the_camera_file(tmp_path):
    store = Store(tmp_path / "skyflock.sqlite")
    store.insert_state_vector(StateVector(time=10, icao24="abc123", position_source=0))
    removed = store.flush_sightings()
    assert removed["state_vectors"] == 1
    assert store.conn.execute("SELECT COUNT(*) FROM state_vectors").fetchone()[0] == 0
    assert (tmp_path / "skyflock.sqlite").is_file()


def test_pipeline_masks_and_tracks_an_unknown_light():
    import numpy as np

    camera = rig_pointed_at(45, 30)
    camera = camera.model_copy(
        update={"sky_calibrated": True, "sky_mask": [(0, 0), (30, 0), (30, 30), (0, 30)]}
    )
    store = Store(":memory:")
    pipeline = Pipeline(
        camera,
        WorkloadConfig(max_fps=100, drop_frames=False, threshold=200, cell=4),
        store,
        detector=None,
        matchers=[],
    )
    from skyflock.detect.host import load_detector

    pipeline.detector = load_detector(WorkloadConfig(threshold=200, cell=4))
    frame = np.zeros((40, 40), dtype=np.uint8)
    frame[4:6, 4:6] = 255
    frame[35:37, 35:37] = 255
    found = pipeline.process_frame(frame, 10, [])
    assert len(found) == 1
    assert found[0].spot.x < 30
    assert found[0].vector.optical.knowledge == Knowledge.UNKNOWN
    assert pipeline.process_frame(np.zeros((40, 40), dtype=np.uint8), 20, []) == []
    flights = store.conn.execute("SELECT COUNT(*) AS n FROM flights").fetchone()["n"]
    assert flights == 1
