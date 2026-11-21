import numpy as np

from skyflock.calib.mask import SkyMask
from skyflock.calib.plate import parse_solve_field_output
from skyflock.calib.pose import fit_pose
from skyflock.camera.config import CameraConfig, Mount, Site, load_camera
from skyflock.detect.host import load_detector, provider_list
from skyflock.detect.workload import CapturePace, FpsGate, WorkloadConfig, load_workload
from skyflock.geo import azel_to_pixel, pixel_to_azel, wrap180
from skyflock.track.tracker import Tracker


def camera(**overrides) -> CameraConfig:
    payload = dict(
        camera_id="cam",
        width=200,
        height=200,
        focal_px=500,
        site=Site(latitude_deg=0, longitude_deg=0, altitude_m=0),
        mount=Mount(yaw_deg=90, pitch_deg=20, roll_deg=0),
    )
    payload.update(overrides)
    return CameraConfig(**payload)


def test_pixel_and_azimuth_round_trip():
    rig = camera()
    for u, v in ((100, 100), (40, 70), (160, 140)):
        azimuth, elevation = pixel_to_azel(u, v, rig)
        pixel = azel_to_pixel(azimuth, elevation, rig)
        assert pixel is not None
        assert abs(pixel[0] - u) < 1e-4
        assert abs(pixel[1] - v) < 1e-4


def test_aim_puts_a_known_direction_on_the_clicked_pixel():
    from skyflock.calib.sky import aim_at_pixel, focal_px_for_horizontal_fov, horizontal_fov_deg

    rig = camera()
    azimuth, elevation = pixel_to_azel(40, 70, rig)
    scrambled = rig.model_copy(
        update={"mount": rig.mount.model_copy(update={"yaw_deg": rig.mount.yaw_deg + 12, "pitch_deg": rig.mount.pitch_deg - 8})}
    )
    aimed = aim_at_pixel(scrambled, azimuth, elevation, 40, 70)
    pixel = azel_to_pixel(azimuth, elevation, aimed)
    assert pixel is not None
    assert abs(pixel[0] - 40) < 0.5
    assert abs(pixel[1] - 70) < 0.5
    assert abs(aimed.mount.roll_deg - rig.mount.roll_deg) < 1e-9
    focal = focal_px_for_horizontal_fov(640, 70)
    assert abs(focal - 457.0) < 0.2
    wide = camera(width=640, height=480, focal_px=focal)
    assert abs(horizontal_fov_deg(wide) - 70) < 0.05


def test_sun_is_up_at_local_noon_and_down_at_midnight():
    from datetime import datetime, timezone

    from skyflock.calib.sky import body_azel

    site = Site(latitude_deg=0, longitude_deg=0, altitude_m=0)
    noon = datetime(2024, 3, 20, 12, 0, tzinfo=timezone.utc).timestamp()
    midnight = datetime(2024, 3, 20, 0, 0, tzinfo=timezone.utc).timestamp()
    _noon_az, noon_el = body_azel("Sun", site, noon)
    _night_az, night_el = body_azel("Sun", site, midnight)
    assert noon_el > 60
    assert night_el < 0
    _moon_az, moon_el = body_azel("Moon", site, noon)
    assert -90 <= moon_el <= 90
    _star_az, star_el = body_azel("Vega", site, noon)
    assert -90 <= star_el <= 90


def test_sky_mask_keeps_only_the_polygon():
    mask = SkyMask([(0, 0), (100, 0), (100, 40), (0, 40)])
    assert mask.allows(10, 10)
    assert not mask.allows(10, 80)
    assert SkyMask([]).allows(1000, 1000)


def test_solve_field_text_parses():
    text = """
    Field center: (RA,Dec) = (123.4, -5.6) deg.
    Field size: 30.0 x 20.0 arcminutes
    Field rotation angle: up is 12.5 degrees E of N
    """
    plate = parse_solve_field_output(text)
    assert plate.ra_deg == 123.4
    assert plate.dec_deg == -5.6
    assert plate.orientation_deg == 12.5
    assert plate.width_arcmin == 30.0


def test_adsb_pose_fit_recovers_mount():
    truth = camera()
    pairs = []
    for u, v in ((70, 80), (100, 100), (140, 90), (80, 140), (150, 150)):
        azimuth, elevation = pixel_to_azel(u, v, truth)
        pairs.append((azimuth, elevation, u, v))
    guess = truth.model_copy(
        update={
            "mount": truth.mount.model_copy(
                update={"yaw_deg": truth.mount.yaw_deg + 2, "pitch_deg": truth.mount.pitch_deg - 1}
            )
        }
    )
    fitted = fit_pose(pairs, guess)
    assert abs(wrap180(fitted.mount.yaw_deg - truth.mount.yaw_deg)) < 0.05
    assert abs(fitted.mount.pitch_deg - truth.mount.pitch_deg) < 0.05


def test_provider_list_and_threshold_detector():
    assert provider_list("directml")[0] == "DmlExecutionProvider"
    assert provider_list("cuda")[0] == "CUDAExecutionProvider"
    frame = np.zeros((30, 30), dtype=np.uint8)
    frame[10:14, 12:16] = 255
    detector = load_detector(WorkloadConfig(threshold=200, cell=8))
    found = detector.detect(frame)
    assert len(found) == 1
    assert 12 <= found[0].x <= 16
    assert 10 <= found[0].y <= 14


def test_tracklet_closes_into_a_flight():
    tracker = Tracker(gate_px=20, max_gap_s=2)
    from skyflock.detect.types import Detection

    tracker.update(10, [Detection(x=5, y=5, w=2, h=2)])
    tracker.update(11, [Detection(x=8, y=6, w=2, h=2)])
    assert tracker.close_stale(12) == []
    flights = tracker.close_stale(14)
    assert len(flights) == 1
    assert flights[0].firstseen == 10
    assert flights[0].lastseen == 11
    assert flights[0].day == 0
    assert len(flights[0].track) == 2


def test_capture_pace_ramps_after_a_spot_and_falls_back():
    settings = WorkloadConfig(idle_fps=1, active_fps=15, hold_seconds=4)
    pace = CapturePace()
    assert pace.observe(0, 0, settings) == "idle"
    assert pace.interval(settings) == 1
    assert pace.observe(3, 10, settings) == "active"
    assert pace.fps(settings) == 15
    assert pace.observe(0, 13, settings) == "active"
    assert pace.observe(0, 14.1, settings) == "idle"
    assert pace.interval(settings) == 1


def test_fps_gate_drops_frames():
    gate = FpsGate(WorkloadConfig(max_fps=1, drop_frames=True))
    assert gate.allow(0)
    assert not gate.allow(0.2)
    assert gate.allow(1.1)


def test_calibration_page_aims_the_live_camera(tmp_path):
    import time
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    from skyflock.api.app import make_handler
    from skyflock.calib.sky import body_azel, body_names
    from skyflock.store.db import Store

    rig = camera(width=640, height=480, focal_px=457, site=Site(latitude_deg=40, longitude_deg=-75, altitude_m=0))
    path = tmp_path / "camera.local.yaml"
    chosen = None
    for name in body_names():
        _azimuth, elevation = body_azel(name, rig.site, time.time())
        if elevation > 15:
            chosen = name
            break
    assert chosen is not None
    status = {"camera_config": rig, "camera": str(path), "tracking": False}
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Store(":memory:"), status=status))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        payload = urlencode(
            {
                "latitude_deg": 40,
                "longitude_deg": -75,
                "altitude_m": 0,
                "fov_deg": 70,
                "body": chosen,
                "pixel_x": 200,
                "pixel_y": 160,
                "action": "aim",
            }
        ).encode()
        request = Request(f"http://127.0.0.1:{port}/calibrate", data=payload, method="POST")
        with urlopen(request) as response:
            page = response.read().decode("utf-8")
        azimuth, elevation = body_azel(chosen, rig.site, time.time())
        pixel = azel_to_pixel(azimuth, elevation, rig)
        assert response.status == 200
        assert chosen in page
        assert pixel is not None
        assert abs(pixel[0] - 200) < 1
        assert abs(pixel[1] - 160) < 1
        assert rig.sky_calibrated is False
        assert rig.ready_to_track() is False
        assert "focal_px:" in path.read_text(encoding="utf-8")
    finally:
        server.shutdown()
        server.server_close()


def test_webcam_config_round_trip(tmp_path):
    from skyflock.capture import CameraDevice, dump_camera, webcam_config
    from skyflock.camera.config import load_camera

    camera = webcam_config(CameraDevice(index=0, width=1280, height=720))
    path = tmp_path / "camera.local.yaml"
    dump_camera(camera, path)
    loaded = load_camera(path)
    assert loaded.camera_id == "win-cam-0"
    assert loaded.width == 1280
    assert loaded.source == 0
    assert loaded.focal_px > 0


def test_example_yaml_loads():
    rig = load_camera("config/camera.example.yaml")
    assert rig.zoom_fixed
    assert rig.lens_name == "telephoto-400"
    workload = load_workload("config/workload.example.yaml")
    assert workload.normalized_device() == "cpu"
