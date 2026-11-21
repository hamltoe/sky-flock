"""Site geometry: WGS84 azimuth, equatorial to horizontal, and the pinhole plate."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from skyflock.camera.config import CameraConfig

WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)


def wrap360(degrees: float) -> float:
    return degrees % 360.0


def wrap180(degrees: float) -> float:
    return (degrees + 180.0) % 360.0 - 180.0


def julian_date(unix_time: float) -> float:
    return unix_time / 86400.0 + 2440587.5


def gmst_deg(unix_time: float) -> float:
    """Greenwich mean sidereal time in degrees. Accurate to well under an arcminute."""
    jd = julian_date(unix_time)
    t = (jd - 2451545.0) / 36525.0
    gmst = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * t * t
        - (t * t * t) / 38710000.0
    )
    return wrap360(gmst)


def geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_m: float) -> tuple[float, float, float]:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    x = (n + alt_m) * cos_lat * math.cos(lon)
    y = (n + alt_m) * cos_lat * math.sin(lon)
    z = (n * (1.0 - WGS84_E2) + alt_m) * sin_lat
    return x, y, z


def observer_azel(
    obs_lat: float,
    obs_lon: float,
    obs_alt_m: float,
    tgt_lat: float,
    tgt_lon: float,
    tgt_alt_m: float,
) -> tuple[float, float]:
    """Azimuth (degrees from true north toward east) and elevation of a WGS84 target."""
    ox, oy, oz = geodetic_to_ecef(obs_lat, obs_lon, obs_alt_m)
    tx, ty, tz = geodetic_to_ecef(tgt_lat, tgt_lon, tgt_alt_m)
    dx, dy, dz = tx - ox, ty - oy, tz - oz
    lat = math.radians(obs_lat)
    lon = math.radians(obs_lon)
    east = -math.sin(lon) * dx + math.cos(lon) * dy
    north = -math.sin(lat) * math.cos(lon) * dx - math.sin(lat) * math.sin(lon) * dy + math.cos(lat) * dz
    up = math.cos(lat) * math.cos(lon) * dx + math.cos(lat) * math.sin(lon) * dy + math.sin(lat) * dz
    return enu_to_azel((east, north, up))


def azel_to_enu(az_deg: float, el_deg: float) -> tuple[float, float, float]:
    az = math.radians(az_deg)
    el = math.radians(el_deg)
    east = math.cos(el) * math.sin(az)
    north = math.cos(el) * math.cos(az)
    up = math.sin(el)
    return east, north, up


def enu_to_azel(enu: tuple[float, float, float]) -> tuple[float, float]:
    east, north, up = enu
    horiz = math.hypot(east, north)
    az = wrap360(math.degrees(math.atan2(east, north)))
    el = math.degrees(math.atan2(up, horiz))
    return az, el


def radec_to_azel(
    ra_deg: float,
    dec_deg: float,
    lat_deg: float,
    lon_deg: float,
    unix_time: float,
) -> tuple[float, float]:
    """Apparent horizontal place, ignoring refraction. RA and Dec are degrees."""
    lst = wrap360(gmst_deg(unix_time) + lon_deg)
    hour_angle = math.radians(wrap180(lst - ra_deg))
    dec = math.radians(dec_deg)
    lat = math.radians(lat_deg)
    sin_el = math.sin(dec) * math.sin(lat) + math.cos(dec) * math.cos(lat) * math.cos(hour_angle)
    sin_el = max(-1.0, min(1.0, sin_el))
    el = math.asin(sin_el)
    cos_el = math.cos(el)
    if abs(cos_el) < 1e-12:
        az = 0.0
    else:
        sin_az = (-math.sin(hour_angle) * math.cos(dec)) / cos_el
        cos_az = (
            math.sin(dec) * math.cos(lat) - math.cos(dec) * math.sin(lat) * math.cos(hour_angle)
        ) / cos_el
        az = math.atan2(sin_az, cos_az)
    return wrap360(math.degrees(az)), math.degrees(el)


def angular_separation_deg(az1: float, el1: float, az2: float, el2: float) -> float:
    v1 = azel_to_enu(az1, el1)
    v2 = azel_to_enu(az2, el2)
    dot = max(-1.0, min(1.0, v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2]))
    return math.degrees(math.acos(dot))


def _cross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _scale(a: tuple[float, float, float], k: float) -> tuple[float, float, float]:
    return a[0] * k, a[1] * k, a[2] * k


def _add(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return a[0] + b[0], a[1] + b[1], a[2] + b[2]


def _unit(a: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.sqrt(_dot(a, a))
    if n == 0:
        return a
    return _scale(a, 1.0 / n)


def _elevation_up(az_deg: float, el_deg: float) -> tuple[float, float, float]:
    az = math.radians(az_deg)
    el = math.radians(el_deg)
    return (
        -math.sin(el) * math.sin(az),
        -math.sin(el) * math.cos(az),
        math.cos(el),
    )


def camera_axes(
    yaw_deg: float, pitch_deg: float, roll_deg: float
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """Camera basis in ENU. +x is right, +y is down the image, +z is the optical axis."""
    z_axis = _unit(azel_to_enu(yaw_deg, pitch_deg))
    y_down = _unit(_scale(_elevation_up(yaw_deg, pitch_deg), -1.0))
    x_right = _unit(_cross(y_down, z_axis))
    roll = math.radians(roll_deg)
    cr, sr = math.cos(roll), math.sin(roll)
    x_axis = _unit(_add(_scale(x_right, cr), _scale(y_down, sr)))
    y_axis = _unit(_add(_scale(_scale(x_right, -1.0), sr), _scale(y_down, cr)))
    return x_axis, y_axis, z_axis


def _distort(x: float, y: float, k1: float) -> tuple[float, float]:
    if not k1:
        return x, y
    scale = 1.0 + k1 * (x * x + y * y)
    return x * scale, y * scale


def _undistort(x: float, y: float, k1: float) -> tuple[float, float]:
    if not k1:
        return x, y
    xu, yu = x, y
    for _ in range(6):
        scale = 1.0 + k1 * (xu * xu + yu * yu)
        if scale == 0:
            break
        xu, yu = x / scale, y / scale
    return xu, yu


def pixel_to_azel(u: float, v: float, camera: CameraConfig) -> tuple[float, float]:
    focal = camera.resolved_focal_px()
    cx, cy = camera.principal_point()
    x_axis, y_axis, z_axis = camera_axes(camera.mount.yaw_deg, camera.mount.pitch_deg, camera.mount.roll_deg)
    cam_x, cam_y = _undistort((u - cx) / focal, (v - cy) / focal, camera.distortion_k1)
    direction = _add(_add(_scale(x_axis, cam_x), _scale(y_axis, cam_y)), z_axis)
    return enu_to_azel(direction)


def azel_to_pixel(az_deg: float, el_deg: float, camera: CameraConfig) -> tuple[float, float] | None:
    focal = camera.resolved_focal_px()
    cx, cy = camera.principal_point()
    x_axis, y_axis, z_axis = camera_axes(camera.mount.yaw_deg, camera.mount.pitch_deg, camera.mount.roll_deg)
    enu = azel_to_enu(az_deg, el_deg)
    cam_z = _dot(enu, z_axis)
    if cam_z <= 1e-9:
        return None
    cam_x = _dot(enu, x_axis) / cam_z
    cam_y = _dot(enu, y_axis) / cam_z
    cam_x, cam_y = _distort(cam_x, cam_y, camera.distortion_k1)
    return cx + focal * cam_x, cy + focal * cam_y


def in_field(
    az_deg: float,
    el_deg: float,
    camera: CameraConfig,
    margin_deg: float = 0.0,
) -> bool:
    if el_deg < -margin_deg:
        return False
    separation = angular_separation_deg(az_deg, el_deg, camera.mount.yaw_deg, camera.mount.pitch_deg)
    return separation <= (camera.fov_deg() / 2.0) + margin_deg
