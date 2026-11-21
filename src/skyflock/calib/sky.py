"""Aim a camera at the Sun, the Moon, or a bright star, and set the lens field of view."""

from __future__ import annotations

import math

from skyflock.camera.config import CameraConfig, Site
from skyflock.geo import azel_to_pixel, julian_date, radec_to_azel, wrap360

# J2000 places of stars bright enough to center by eye. RA and Dec are degrees.
BRIGHT_STARS: tuple[tuple[str, float, float], ...] = (
    ("Sirius", 101.287, -16.716),
    ("Canopus", 95.988, -52.696),
    ("Arcturus", 213.915, 19.182),
    ("Vega", 279.234, 38.784),
    ("Capella", 79.172, 45.998),
    ("Rigel", 78.634, -8.202),
    ("Procyon", 114.825, 5.225),
    ("Betelgeuse", 88.793, 7.407),
    ("Altair", 297.696, 8.868),
    ("Aldebaran", 68.980, 16.509),
    ("Spica", 201.298, -11.161),
    ("Antares", 247.352, -26.432),
    ("Pollux", 116.329, 28.026),
    ("Deneb", 310.358, 45.280),
    ("Regulus", 152.093, 11.967),
    ("Polaris", 37.955, 89.264),
)


def body_names() -> list[str]:
    return ["Sun", "Moon", *[name for name, _ra, _dec in BRIGHT_STARS]]


def horizontal_fov_deg(camera: CameraConfig) -> float:
    focal = camera.resolved_focal_px()
    return math.degrees(2.0 * math.atan((camera.width / 2.0) / focal))


def focal_px_for_horizontal_fov(width: int, fov_deg: float) -> float:
    if not 1.0 < fov_deg < 170.0:
        raise ValueError("horizontal field of view must be between 1 and 170 degrees")
    return (width / 2.0) / math.tan(math.radians(fov_deg) / 2.0)


def body_azel(name: str, site: Site, unix_time: float) -> tuple[float, float]:
    """Azimuth and elevation of a named body. Sun and Moon use a low-precision analytic place."""
    ra, dec = _radec(name, unix_time)
    return radec_to_azel(ra, dec, site.latitude_deg, site.longitude_deg, unix_time)


def aim_at_pixel(camera: CameraConfig, azimuth_deg: float, elevation_deg: float, pixel_x: float, pixel_y: float) -> CameraConfig:
    """Point the mount so the body lands on the clicked pixel. Roll stays as it is."""
    if elevation_deg < 0:
        raise ValueError("that body is below the horizon from this site")
    yaw = wrap360(azimuth_deg)
    pitch = float(max(-85.0, min(85.0, elevation_deg)))
    for _ in range(40):
        projected = azel_to_pixel(azimuth_deg, elevation_deg, _with_mount(camera, yaw, pitch))
        if projected is None:
            break
        du = projected[0] - pixel_x
        dv = projected[1] - pixel_y
        if math.hypot(du, dv) < 0.2:
            return _with_mount(camera, yaw, pitch)
        step = 0.05
        bumped_yaw = azel_to_pixel(azimuth_deg, elevation_deg, _with_mount(camera, yaw + step, pitch))
        bumped_pitch = azel_to_pixel(azimuth_deg, elevation_deg, _with_mount(camera, yaw, pitch + step))
        if bumped_yaw is None or bumped_pitch is None:
            break
        j00 = (bumped_yaw[0] - projected[0]) / step
        j10 = (bumped_yaw[1] - projected[1]) / step
        j01 = (bumped_pitch[0] - projected[0]) / step
        j11 = (bumped_pitch[1] - projected[1]) / step
        determinant = j00 * j11 - j01 * j10
        if abs(determinant) < 1e-9:
            break
        dyaw = max(-10.0, min(10.0, (-du * j11 + dv * j01) / determinant))
        dpitch = max(-10.0, min(10.0, (-j00 * dv + j10 * du) / determinant))
        yaw = wrap360(yaw + dyaw)
        pitch = float(max(-89.0, min(89.0, pitch + dpitch)))
    raise ValueError("could not place that body on the clicked pixel with this field of view")


def _with_mount(camera: CameraConfig, yaw: float, pitch: float) -> CameraConfig:
    mount = camera.mount.model_copy(update={"yaw_deg": yaw, "pitch_deg": pitch})
    return camera.model_copy(update={"mount": mount, "calibration_version": "sky-body"})


def _radec(name: str, unix_time: float) -> tuple[float, float]:
    if name == "Sun":
        return _sun_radec(unix_time)
    if name == "Moon":
        return _moon_radec(unix_time)
    for star, ra, dec in BRIGHT_STARS:
        if star == name:
            return ra, dec
    raise ValueError(f"unknown sky body {name}")


def _sun_radec(unix_time: float) -> tuple[float, float]:
    """Schlyter's low-precision solar place. Error is about an arcminute."""
    day = julian_date(unix_time) - 2451543.5
    mean_anomaly = math.radians(wrap360(356.0470 + 0.9856002585 * day))
    ecliptic_lon = math.radians(
        wrap360(
            282.9404
            + 4.70935e-5 * day
            + math.degrees(mean_anomaly)
            + 1.915 * math.sin(mean_anomaly)
            + 0.020 * math.sin(2 * mean_anomaly)
        )
    )
    obliquity = math.radians(23.4393 - 3.563e-7 * day)
    ra = math.degrees(math.atan2(math.cos(obliquity) * math.sin(ecliptic_lon), math.cos(ecliptic_lon)))
    dec = math.degrees(math.asin(math.sin(obliquity) * math.sin(ecliptic_lon)))
    return wrap360(ra), dec


def _moon_radec(unix_time: float) -> tuple[float, float]:
    """Schlyter's low-precision lunar place, close enough to center the Moon in a webcam."""
    day = julian_date(unix_time) - 2451543.5
    node = wrap360(125.1228 - 0.0529538083 * day)
    inclination = 5.1454
    argument = wrap360(318.0634 + 0.1643573223 * day)
    eccentricity = 0.054900
    anomaly = wrap360(115.3654 + 13.0649929509 * day)
    eccentric = anomaly
    for _ in range(8):
        eccentric = anomaly + math.degrees(eccentricity) * math.sin(math.radians(eccentric))
    true_anomaly = math.degrees(
        math.atan2(
            math.sqrt(1 - eccentricity * eccentricity) * math.sin(math.radians(eccentric)),
            math.cos(math.radians(eccentric)) - eccentricity,
        )
    )
    sun_anomaly = wrap360(356.0470 + 0.9856002585 * day)
    sun_lon = wrap360(sun_anomaly + 282.9404 + 4.70935e-5 * day)
    moon_lon = wrap360(node + argument + anomaly)
    elongation = wrap360(moon_lon - sun_lon)
    node_distance = wrap360(moon_lon - node)
    longitude = math.degrees(math.atan2(
        math.sin(math.radians(node)) * math.cos(math.radians(true_anomaly + argument))
        + math.cos(math.radians(node)) * math.sin(math.radians(true_anomaly + argument)) * math.cos(math.radians(inclination)),
        math.cos(math.radians(node)) * math.cos(math.radians(true_anomaly + argument))
        - math.sin(math.radians(node)) * math.sin(math.radians(true_anomaly + argument)) * math.cos(math.radians(inclination)),
    ))
    latitude = math.degrees(math.asin(math.sin(math.radians(true_anomaly + argument)) * math.sin(math.radians(inclination))))
    longitude += (
        -1.274 * _sin(anomaly - 2 * elongation)
        + 0.658 * _sin(2 * elongation)
        - 0.186 * _sin(sun_anomaly)
        - 0.059 * _sin(2 * anomaly - 2 * elongation)
        - 0.057 * _sin(anomaly - 2 * elongation + sun_anomaly)
        + 0.053 * _sin(anomaly + 2 * elongation)
        + 0.046 * _sin(2 * elongation - sun_anomaly)
        + 0.041 * _sin(anomaly - sun_anomaly)
        - 0.035 * _sin(elongation)
        - 0.031 * _sin(anomaly + sun_anomaly)
        - 0.015 * _sin(2 * node_distance - 2 * elongation)
        + 0.011 * _sin(anomaly - 4 * elongation)
    )
    latitude += (
        -0.173 * _sin(node_distance - 2 * elongation)
        - 0.055 * _sin(anomaly - node_distance - 2 * elongation)
        - 0.046 * _sin(anomaly + node_distance - 2 * elongation)
        + 0.033 * _sin(node_distance + 2 * elongation)
        + 0.017 * _sin(2 * anomaly + node_distance)
    )
    obliquity = math.radians(23.4393 - 3.563e-7 * day)
    lon = math.radians(wrap360(longitude))
    lat = math.radians(latitude)
    equatorial_y = math.cos(lat) * math.sin(lon) * math.cos(obliquity) - math.sin(lat) * math.sin(obliquity)
    equatorial_x = math.cos(lat) * math.cos(lon)
    equatorial_z = math.cos(lat) * math.sin(lon) * math.sin(obliquity) + math.sin(lat) * math.cos(obliquity)
    ra = math.degrees(math.atan2(equatorial_y, equatorial_x))
    dec = math.degrees(math.atan2(equatorial_z, math.hypot(equatorial_x, equatorial_y)))
    return wrap360(ra), dec


def _sin(degrees: float) -> float:
    return math.sin(math.radians(degrees))
