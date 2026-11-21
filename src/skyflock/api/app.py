"""Local read API with the OpenSky `GET /states/all` response shape."""

from __future__ import annotations

import html
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from pydantic import ValidationError

from skyflock.calib.sky import (
    aim_at_pixel,
    body_azel,
    body_names,
    focal_px_for_horizontal_fov,
    horizontal_fov_deg,
)
from skyflock.camera.config import copy_config
from skyflock.capture import dump_camera
from skyflock.detect.workload import WorkloadConfig
from skyflock.opensky.io import build_states_payload
from skyflock.store.db import Store


def make_handler(store: Store, *, preview_path: str | None = None, status: dict | None = None):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            self._form = parse_qs(raw)
            self.do_GET()

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/setup"}:
                body = _status_page(store, status).encode("utf-8")
                self._send(200, "text/html; charset=utf-8", body)
                return
            if parsed.path == "/settings":
                self._settings(parsed)
                return
            if parsed.path == "/calibrate":
                self._calibrate(parsed)
                return
            if parsed.path == "/status":
                body = json.dumps(_status_payload(store, status)).encode("utf-8")
                self._send(200, "application/json", body, cache=False)
                return
            if parsed.path == "/admin/flush" and self.command == "POST":
                store.flush_sightings()
                self.send_response(303)
                self.send_header("Location", "/")
                self.end_headers()
                return
            if parsed.path == "/preview.jpg":
                image = None if status is None else status.get("preview_jpeg")
                if not image:
                    image = _preview_bytes(preview_path)
                if image is None:
                    self.send_error(404)
                    return
                self._send(200, "image/jpeg", image, cache=False)
                return
            if parsed.path not in {"/states", "/states/all", "/api/states/all"}:
                self.send_error(404)
                return
            query = parse_qs(parsed.query)
            bbox = _bbox(query)
            icao = query.get("icao24", [None])[0]
            when = query.get("time", [None])[0]
            vectors = store.query_states(
                time=int(when) if when is not None else None,
                icao24=icao,
                bbox=bbox,
            )
            stamp = int(when) if when is not None else (vectors[0].time if vectors else 0)
            payload = build_states_payload(vectors, time=stamp)
            browser = parsed.path == "/states" or "text/html" in self.headers.get("Accept", "")
            if browser and parsed.path != "/api/states/all":
                body = _states_page(payload).encode("utf-8")
                self._send(200, "text/html; charset=utf-8", body)
                return
            body = json.dumps(payload).encode("utf-8")
            self._send(200, "application/json", body)

        def _settings(self, parsed) -> None:
            box = None if status is None else status.get("workload_box")
            current = box.config if box is not None else WorkloadConfig()
            notice = ""
            if self.command == "POST":
                if box is None:
                    notice = "Start the camera viewer before saving capture settings."
                else:
                    try:
                        current = box.apply(_posted_settings(getattr(self, "_form", {}), current))
                    except (TypeError, ValueError, ValidationError) as exc:
                        notice = str(exc)
                    else:
                        self.send_response(303)
                        self.send_header("Location", "/settings?saved=1")
                        self.end_headers()
                        return
            elif parse_qs(parsed.query).get("saved", [""])[0] == "1":
                notice = "Capture settings saved. The camera is using them now."
            body = _settings_page(current, status, notice).encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)

        def _calibrate(self, parsed) -> None:
            camera = None if status is None else status.get("camera_config")
            path = "" if status is None else status.get("camera", "")
            selected = parse_qs(parsed.query).get("body", ["Sun"])[0]
            notice = "" if status is None else status.get("calibration_notice", "")
            if self.command == "POST":
                form = getattr(self, "_form", {})
                selected = form.get("body", [selected])[0]
                if camera is None:
                    notice = "Start the camera viewer before calibrating."
                else:
                    try:
                        updated, notice = _apply_calibration(camera, form)
                        copy_config(camera, updated)
                        if path:
                            dump_camera(camera, path)
                        if status is not None:
                            status["tracking"] = camera.ready_to_track()
                            status["calibration_notice"] = notice
                        self.send_response(303)
                        self.send_header("Location", "/calibrate?body=" + selected.replace(" ", "%20"))
                        self.end_headers()
                        return
                    except (TypeError, ValueError, ValidationError) as exc:
                        notice = str(exc)
                        if status is not None:
                            status["calibration_notice"] = notice
            elif status is not None and "calibration_notice" in status and parse_qs(parsed.query).get("body"):
                notice = status.get("calibration_notice", "")
            if camera is None:
                body = _calibrate_page(None, selected, notice or "Start the camera viewer before calibrating.").encode("utf-8")
            else:
                body = _calibrate_page(camera, selected, notice).encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)

        def _send(self, code: int, content_type: str, body: bytes, *, cache: bool = True) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if not cache:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:
            return

    return Handler


def serve(
    store: Store,
    host: str = "127.0.0.1",
    port: int = 8080,
    *,
    preview_path: str | None = None,
    status: dict | None = None,
) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(store, preview_path=preview_path, status=status))
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _preview_bytes(preview_path: str | None) -> bytes | None:
    if not preview_path:
        return None
    from pathlib import Path

    file = Path(preview_path)
    if not file.is_file():
        return None
    return file.read_bytes()


def _status_payload(store: Store, status: dict | None) -> dict:
    frames = 0 if status is None else status.get("frames", 0)
    spots = 0 if status is None else status.get("spots", 0)
    error = "" if status is None or not status.get("error") else status["error"]
    count = store.conn.execute("SELECT COUNT(*) AS n FROM state_vectors").fetchone()["n"]
    tracking = False if status is None else bool(status.get("tracking"))
    if error:
        message = error
    elif tracking:
        message = "Sky calibration is loaded. Bright spots in the sky mask are being matched."
    else:
        message = "Tracking is off. Calibrate this camera to the sky before any matching starts."
    return {
        "frames": frames,
        "spots": spots,
        "sightings": count,
        "tracking": tracking,
        "message": message,
        "db": "skyflock.sqlite" if status is None else status.get("db", "skyflock.sqlite"),
        "preview": "" if status is None else status.get("preview", ""),
        "camera": "" if status is None else status.get("camera", ""),
        "pace": "idle" if status is None else status.get("pace", "idle"),
        "capture_fps": 1 if status is None else status.get("capture_fps", 1),
    }


def _status_page(store: Store, status: dict | None) -> str:
    payload = _status_payload(store, status)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Sky Flock camera</title>
</head>
<body style="font-family: sans-serif; background: #111; color: #eee; margin: 2rem;">
  <h1>Sky Flock</h1>
  <p id="message">{payload["message"]}</p>
  <p id="counts">Frames read: {payload["frames"]}. Spots in the last frame: {payload["spots"]}. Stored sightings: {payload["sightings"]}.</p>
  <p id="pace">Capture pace: {payload["pace"]} at {payload["capture_fps"]} frames per second.</p>
  <p>Database: {payload["db"]}</p>
  <p>Preview file: {payload["preview"]}</p>
  <p>Camera config: {payload["camera"]}</p>
  <img id="preview" src="/preview.jpg" alt="Latest camera frame" style="max-width: 100%; background: #000;">
  <p><a href="/calibrate" style="color: #9cf;">Calibrate to the sky</a></p>
  <p><a href="/settings" style="color: #9cf;">Capture settings</a></p>
  <p><a href="/states" style="color: #9cf;">Latest state vectors</a></p>
  <form method="post" action="/admin/flush">
    <button type="submit">Flush stored sightings</button>
  </form>
  <script>
    async function refreshView() {{
      const image = document.getElementById("preview");
      image.src = "/preview.jpg?t=" + Date.now();
      try {{
        const response = await fetch("/status", {{cache: "no-store"}});
        const data = await response.json();
        document.getElementById("message").textContent = data.message;
        document.getElementById("counts").textContent =
          "Frames read: " + data.frames + ". Spots in the last frame: " + data.spots + ". Stored sightings: " + data.sightings + ".";
        document.getElementById("pace").textContent =
          "Capture pace: " + data.pace + " at " + data.capture_fps + " frames per second.";
      }} catch (error) {{
        document.getElementById("message").textContent = "The viewer lost contact with the camera process.";
      }}
    }}
    setInterval(refreshView, 1000);
  </script>
</body>
</html>
"""


def _posted_settings(form: dict, current: WorkloadConfig) -> WorkloadConfig:
    def raw(name: str) -> str:
        return form.get(name, [""])[0].strip()

    update: dict = {}
    for name in ("idle_fps", "active_fps", "hold_seconds", "threshold"):
        text = raw(name)
        if text:
            update[name] = float(text)
    cell = raw("cell")
    if cell:
        update["cell"] = int(cell)
    update["max_fps"] = update.get("active_fps", current.active_fps)
    return WorkloadConfig.model_validate(current.model_copy(update=update).model_dump())


def _settings_page(settings: WorkloadConfig, status: dict | None, notice: str) -> str:
    pace = "idle" if status is None else status.get("pace", "idle")
    fps = 1 if status is None else status.get("capture_fps", settings.idle_fps)
    note = f"<p>{html.escape(notice)}</p>" if notice else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Sky Flock capture settings</title>
</head>
<body style="font-family: sans-serif; background: #111; color: #eee; margin: 2rem;">
  <h1>Capture settings</h1>
  {note}
  <p>The camera stays at the idle rate while the sky is empty. After a bright spot, it captures at the active rate, then returns to idle once the sky has been empty for the hold time.</p>
  <p>Tracking stays off until this camera is calibrated to the sky, so the pace stays idle until then.</p>
  <p>Current pace: {html.escape(str(pace))} at {html.escape(str(fps))} frames per second.</p>
  <form method="post" action="/settings">
    <p><label>Idle rate, frames per second <input name="idle_fps" type="number" min="0.1" max="60" step="0.1" value="{settings.idle_fps}" required></label></p>
    <p><label>Active rate, frames per second <input name="active_fps" type="number" min="0.1" max="60" step="0.1" value="{settings.active_fps}" required></label></p>
    <p><label>Hold the active rate after the last spot, seconds <input name="hold_seconds" type="number" min="0" max="300" step="0.5" value="{settings.hold_seconds}" required></label></p>
    <p><label>Brightness threshold, 0 to 255 <input name="threshold" type="number" min="0" max="255" step="1" value="{settings.threshold}" required></label></p>
    <p><label>Grouping cell, pixels <input name="cell" type="number" min="1" max="128" step="1" value="{settings.cell}" required></label></p>
    <p><button type="submit">Save capture settings</button></p>
  </form>
  <p><a href="/" style="color: #9cf;">Back to the camera</a></p>
</body>
</html>
"""


def _field(form: dict, name: str) -> str:
    return form.get(name, [""])[0].strip()


def _apply_calibration(camera, form: dict):
    latitude = float(_field(form, "latitude_deg"))
    longitude = float(_field(form, "longitude_deg"))
    altitude = float(_field(form, "altitude_m") or "0")
    fov = float(_field(form, "fov_deg"))
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("latitude must be between -90 and 90, and longitude between -180 and 180")
    focal = round(focal_px_for_horizontal_fov(camera.width, fov), 2)
    updated = camera.model_copy(
        update={
            "site": camera.site.model_copy(
                update={"latitude_deg": latitude, "longitude_deg": longitude, "altitude_m": altitude}
            ),
            "focal_px": focal,
        }
    )
    if (_field(form, "action") or "save") != "aim":
        return updated, f"Saved the site and a {fov:.1f}° horizontal field of view."
    body = _field(form, "body") or "Sun"
    pixel_x = float(_field(form, "pixel_x"))
    pixel_y = float(_field(form, "pixel_y"))
    if not (0 <= pixel_x <= camera.width and 0 <= pixel_y <= camera.height):
        raise ValueError("click the body inside the picture")
    azimuth, elevation = body_azel(body, updated.site, time.time())
    updated = aim_at_pixel(updated, azimuth, elevation, pixel_x, pixel_y)
    if _field(form, "start_tracking") == "1":
        mask = list(updated.sky_mask)
        if len(mask) < 3:
            width, height = float(camera.width), float(camera.height)
            mask = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
        updated = updated.model_copy(update={"sky_calibrated": True, "sky_mask": mask})
    sentence = (
        f"{body} is at azimuth {azimuth:.1f}° and elevation {elevation:.1f}°. "
        f"The lens is {horizontal_fov_deg(updated):.1f}° wide and the mount is "
        f"yaw {updated.mount.yaw_deg:.1f}°, pitch {updated.mount.pitch_deg:.1f}°."
    )
    if updated.ready_to_track():
        sentence += " Tracking is on."
    else:
        sentence += " Tracking stays off until you mark this view as the sky."
    return updated, sentence


def _calibrate_page(camera, selected: str, notice: str) -> str:
    if camera is None:
        return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Sky Flock calibration</title></head>
<body style="font-family: sans-serif; background: #111; color: #eee; margin: 2rem;">
  <h1>Calibrate to the sky</h1>
  <p>{html.escape(notice)}</p>
  <p><a href="/" style="color: #9cf;">Back to the camera</a></p>
</body></html>"""
    names = body_names()
    if selected not in names:
        selected = "Sun"
    azimuth, elevation = body_azel(selected, camera.site, time.time())
    horizon = "It is below the horizon from this site." if elevation < 0 else "It is above the horizon."
    place = f"{selected} is at azimuth {azimuth:.1f}° and elevation {elevation:.1f}°. {horizon}"
    options = "\n".join(
        f'<option value="{html.escape(name)}"{" selected" if name == selected else ""}>{html.escape(name)}</option>'
        for name in names
    )
    note = f"<p>{html.escape(notice)}</p>" if notice else ""
    fov = horizontal_fov_deg(camera)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Sky Flock calibration</title>
</head>
<body style="font-family: sans-serif; background: #111; color: #eee; margin: 2rem;">
  <h1>Calibrate to the sky</h1>
  {note}
  <p>Set where you are and how wide the lens is, then click the Sun, the Moon, or a bright star in the picture. The mount turns so that body sits on the click.</p>
  <p id="place">{html.escape(place)}</p>
  <form method="post" action="/calibrate">
    <p><label>Latitude <input name="latitude_deg" type="number" min="-90" max="90" step="0.0001" value="{camera.site.latitude_deg}" required></label></p>
    <p><label>Longitude <input name="longitude_deg" type="number" min="-180" max="180" step="0.0001" value="{camera.site.longitude_deg}" required></label></p>
    <p><label>Altitude, metres <input name="altitude_m" type="number" step="1" value="{camera.site.altitude_m}" required></label></p>
    <p><label>Horizontal field of view, degrees <input name="fov_deg" type="number" min="1" max="170" step="0.1" value="{fov:.2f}" required></label></p>
    <p><label>Reference <select name="body" id="body">{options}</select></label></p>
    <p>Click the body in the picture. Pixel <input id="pixel_x" name="pixel_x" type="number" step="0.1"> <input id="pixel_y" name="pixel_y" type="number" step="0.1"></p>
    <div id="frame" style="position: relative; display: inline-block; background: #000;">
      <img id="preview" src="/preview.jpg" alt="Camera picture" data-width="{camera.width}" data-height="{camera.height}" style="max-width: 100%; display: block;">
      <div id="mark" hidden style="position: absolute; width: 12px; height: 12px; margin: -6px 0 0 -6px; border: 2px solid #fc8; border-radius: 50%; pointer-events: none;"></div>
    </div>
    <p><label><input name="start_tracking" type="checkbox" value="1"> This view is the sky. Start tracking bright spots in the whole frame.</label></p>
    <p>
      <button type="submit" name="action" value="save">Save site and lens</button>
      <button type="submit" name="action" value="aim">Aim at the clicked body</button>
    </p>
  </form>
  <p><a href="/" style="color: #9cf;">Back to the camera</a></p>
  <script>
    const image = document.getElementById("preview");
    const mark = document.getElementById("mark");
    image.addEventListener("click", (event) => {{
      const rect = image.getBoundingClientRect();
      const scaleX = Number(image.dataset.width) / rect.width;
      const scaleY = Number(image.dataset.height) / rect.height;
      const x = (event.clientX - rect.left) * scaleX;
      const y = (event.clientY - rect.top) * scaleY;
      document.getElementById("pixel_x").value = x.toFixed(1);
      document.getElementById("pixel_y").value = y.toFixed(1);
      mark.hidden = false;
      mark.style.left = (event.clientX - rect.left) + "px";
      mark.style.top = (event.clientY - rect.top) + "px";
    }});
    document.getElementById("body").addEventListener("change", (event) => {{
      window.location = "/calibrate?body=" + encodeURIComponent(event.target.value);
    }});
    setInterval(() => {{ image.src = "/preview.jpg?t=" + Date.now(); }}, 1000);
  </script>
</body>
</html>
"""


def _states_page(payload: dict) -> str:
    states = payload.get("states") or []
    if states:
        summary = f"{len(states)} state vector{'s' if len(states) != 1 else ''} at time {payload.get('time', 0)}."
    else:
        summary = "No sightings are stored. Tracking stays off until this camera is calibrated to the sky."
    text = html.escape(json.dumps(payload, indent=2))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Sky Flock state vectors</title>
</head>
<body style="font-family: sans-serif; background: #111; color: #eee; margin: 2rem;">
  <h1>Latest state vectors</h1>
  <p>{summary}</p>
  <p><a href="/" style="color: #9cf;">Back to the camera</a></p>
  <pre style="background: #1b1b1b; color: #eee; padding: 1rem; overflow: auto;">{text}</pre>
</body>
</html>
"""


def _bbox(query: dict[str, list[str]]) -> tuple[float, float, float, float] | None:
    keys = ("lamin", "lomin", "lamax", "lomax")
    if not all(key in query for key in keys):
        return None
    lamin, lomin, lamax, lomax = (float(query[key][0]) for key in keys)
    return lamin, lomin, lamax, lomax
