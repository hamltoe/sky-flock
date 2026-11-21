# Sky Flock design

Version 0.1.0. November 2026.

Sky Flock is an open-source camera system that watches the sky, turns bright spots into tracks, and labels each spot as a known aircraft, star, planet, comet, asteroid, or an unknown local object. Measurements are stored in the OpenSky Network state-vector format so an optical sighting can sit beside an ADS-B row.

The package is GPLv3-or-later. Python 3.11 or newer. The first target is a Windows PC with a fixed camera, CPU inference, and an optional CUDA or DirectML device.

This document describes the system as built. It is meant to be shared with another organization that wants to understand the design, reuse the data model, or extend a stage of the pipeline.

## What it is for

The camera is a sky sensor, analogous to a roadside camera that profiles vehicles, limited to objects above the horizon:

- Aircraft, drones, birds, balloons, and lights that do not match a catalog.
- Stars, planets, the Sun, the Moon, comets, and bright asteroids.

It does not look for people, faces, or license plates. A sky mask drops the ground once the camera has been calibrated.

A sighting is either **known**, because a catalog claimed it, or **unknown**, because every catalog missed. Unknown objects keep a local identity. Later catalog knowledge can be attached to the profile from that time forward. Past measurements are not rewritten.

## Pipeline

```
camera frame
  -> capture pace (idle until a spot, then a short fast burst)
  -> sky-calibration gate
  -> detector
  -> sky mask
  -> tracklet
  -> catalog cascade
  -> append-only state vector and, when the track ends, a flight
```

The live command is `skyflock run`. It reads the camera on a background thread and serves a viewer on `127.0.0.1:8080`.

Tracking does not start until both of these are true:

- `sky_calibrated` is set, which happens when an operator aims the camera at the Sun, the Moon, or a bright star and marks the view as sky.
- `sky_mask` has at least three points. Marking the view as sky uses the whole frame until a tighter horizon polygon is drawn.

Before that, the viewer still shows the picture and the capture loop still runs at the idle rate. The detector, tracker, and catalogs are not called, and nothing is written.

## OpenSky data model

Sky Flock follows the OpenSky Network shapes rather than inventing a parallel track format.

Live rows use the 18-field REST order from `GET /states/all`:

`icao24`, `callsign`, `origin_country`, `time_position`, `last_contact`, `longitude`, `latitude`, `baro_altitude`, `on_ground`, `velocity`, `true_track`, `vertical_rate`, `sensors`, `geo_altitude`, `squawk`, `spi`, `position_source`, `category`.

Historical rows follow `state_vectors_data4`: `time`, `icao24`, `lat`, `lon`, `velocity`, `heading`, `vertrate`, `callsign`, `onground`, `alert`, `spi`, `squawk`, `baroaltitude`, `geoaltitude`, `lastposupdate`, `lastcontact`, `serials`, `hour`.

`hour` is the start of the UTC hour. `day` on a flight is UTC midnight of `lastseen`. A vector is fresh when `time - lastcontact` is at most 15 seconds. Position source is `0` ADS-B, `1` ASTERIX, `2` MLAT, `3` FLARM.

Units are the OpenSky units: Unix seconds, WGS84 degrees, metres, metres per second, and heading in degrees true north. `icao24` is lowercase hex and is used only for an OpenSky aircraft match. Stars, planets, comets, and asteroids do not get a fake ICAO address.

An optical sighting that did not come from OpenSky is exported as an object plus an `optical` extension. A row that came from OpenSky, or a historical row that has an `icao24`, no optical block, and a known position source, is exported as the strict 18-element array.

The optical extension sits beside the official columns. It carries camera id, azimuth, elevation, bounding box, right ascension, declination, magnitude, angular residual, and:

| Field | Values |
| --- | --- |
| `identity_type` | `icao`, `star`, `planet`, `comet`, `asteroid`, `local` |
| `knowledge` | `known`, `unknown` |
| `knowledge_source` | `opensky`, `openstartracker`, `planet_ephemeris`, `mpc_comet`, `mpc_asteroid`, or empty |

Closed tracks become `flights_data4` rows: first seen, last seen, callsign, estimated airports when known, and a track of waypoints. An optical track that never got a geographic fix stores null latitude and longitude on those waypoints.

Aircraft metadata is imported from the OpenSky aircraft CSV into an immutable snapshot. Profiles and profile revisions hang off an identity key of the form `identity_type:id`. Promoting an unknown profile updates `profile_id` on later state vectors only. Measurement columns stay as they were recorded.

## Catalog cascade

Each spot is offered to the catalogs in a fixed order. The first catalog that accepts it wins.

1. **Stars.** An OpenStarTracker plate solution can claim pixels directly. On Windows, where that solver is not assumed to be installed, the fallback is a Hipparcos projection through the calibrated camera. OpenStarTracker, when present, speaks a small JSON contract: plate right ascension, declination, orientation, matched stars, and unknown blobs.
2. **OpenSky aircraft**, priority 10, with a wider association gate (default 1 degree) than the sky catalogs.
3. **Sun, Moon, and planets**, priority 20, from Skyfield and the JPL de421 kernel when that extra is installed.
4. **Comets**, priority 30, from MPC `CometEls.txt`. Brightness uses `V = M1 + 5 log10(delta) + 2.5 k1 log10(r)`.
5. **Asteroids**, priority 40, from a magnitude-limited MPCORB scan. The scan is capped, and the absolute-magnitude prefilter keeps only bodies that could be brighter than the camera limit.
6. **Unknown.** A local profile and a state vector are stored. Nothing is dropped just because it is unidentified.

If a star gate and the OpenSky gate both hit, OpenSky wins, because aircraft move and a star solution should not steal an airframe that is inside the wider aircraft gate.

Skyfield, comet elements, asteroid elements, and the Hipparcos file are optional. A missing catalog returns no predictions and the cascade continues.

The OpenSky client uses the public REST API. Anonymous access is the current 10-second sample. Authenticated access uses OAuth2 client credentials and can look one hour back. Bounding boxes are costed the way OpenSky costs them, and the client refuses a box larger than 400 square degrees. Historical Trino access and a local readsb feed are out of this version.

## Geometry

The site is WGS84 latitude, longitude, and altitude. Horizontal coordinates are azimuth from true north toward east, and elevation above the horizon.

The camera is a pinhole. Image `+x` is right, `+y` is down, and `+z` is the optical axis. An optional `k1` radial term is supported. The mount is yaw, pitch, and roll. Yaw is azimuth of the boresight and pitch is its elevation. Focal length is stored in pixels. A horizontal field of view converts to that focal length with `(width / 2) / tan(fov / 2)`.

Equatorial places become horizontal places with Greenwich mean sidereal time. The same transform projects a catalog prediction into the image and a pixel back out to azimuth and elevation.

ADS-B pose fitting is a Gauss-Newton fit of yaw, pitch, roll, and optionally focal length, from control points of `(azimuth, elevation, pixel x, pixel y)`. It needs at least three points. The operator-facing calibration tool uses a smaller solve: one known body and one click, with roll held fixed, which is enough to aim a webcam.

## Calibration

The calibration page is `GET /calibrate`.

The operator enters latitude, longitude, altitude, and horizontal field of view, then chooses the Sun, the Moon, or one of sixteen bright stars (Sirius through Polaris). The page reports that body's azimuth and elevation and whether it is above the horizon. A click on the preview records the pixel. **Aim at the clicked body** moves yaw and pitch so the predicted place lands on that pixel.

Sun and Moon places are analytic approximations (Schlyter), accurate enough to center a body in a webcam. Stars use J2000 right ascension and declination. Skyfield is not required for this page. A body below the horizon is refused.

**Save site and lens** writes the camera YAML and does not aim the mount. **This view is the sky** is opt-in. Checking it sets `sky_calibrated` and, if no horizon polygon exists yet, a rectangle around the whole frame. That is what allows tracking to start. The site coordinates shipped with a Windows webcam setup are placeholders and must be replaced before an aim is meaningful.

A plate solve from `solve-field` text can still be parsed for a later astrometric path. That solver is not required to run the viewer.

## Capture pace

Most sky objects move slowly, so the camera does not run at the sensor rate.

| Setting | Default | Meaning |
| --- | --- | --- |
| Idle rate | 1 frame/s | Used while the frame has no spot |
| Active rate | 15 frames/s | Used after a spot |
| Hold | 4 seconds | How long the fast rate continues after the last spot |
| Brightness threshold | 200 | What the default detector calls a spot |
| Cell | 16 pixels | How nearby bright pixels are grouped |

The grab loop sleeps until the next interval. Frames are JPEG-encoded in memory for the viewer, so a reader does not open a file that is still being written. Settings are edited at `GET /settings`, applied to the running loop, and saved in `config/workload.local.yaml`.

While the camera is uncalibrated, the pace stays idle, because the detector is not running and therefore cannot see a spot.

## Detection

A detector implements `detect(frame)` and returns boxes: center `x`, `y`, width, height, score, and a class label. The default detector is a brightness cluster. It is a reference, not a sky classifier.

An ONNX Runtime host can load a model on CPU, CUDA, or DirectML. Those wheels conflict, so one is installed. The host expects a simple numeric box layout. A Detection Transformer graph is not that layout and needs its own plugin.

Plugins register on the `skyflock.detectors` entry point. The workload file selects one by name.

RF-DETR is a proposed detector, not part of 0.1.0. The Apache 2.0 Nano, Small, Medium, and Large checkpoints can be wrapped as a plugin that emits the same boxes. The XL and 2XL checkpoints use a different license and are out of scope. A COCO-trained checkpoint does not know stars or the Moon. Sky labels still come from the catalog cascade. Segmentation masks and keypoints have no field in the current detection record.

## Storage

The live database is SQLite at `data/skyflock.sqlite` (WAL, busy timeout 5 seconds). The parent directory is created on open.

| Table | Role |
| --- | --- |
| `state_vectors` | One row per sighting, indexed by hour, time, ICAO, local id, and catalog id |
| `flights` | A closed track |
| `aircraft_snapshots`, `aircraft` | Immutable metadata imports |
| `catalog_cache` | Paths of catalog files already seen |
| `profiles`, `profile_revisions`, `profile_promotions` | Identity over time, without rewriting measurements |

The preview image is `data/preview.jpg`. The camera file is `config/camera.local.yaml`. Both are local and are not part of the source tree. The default database name, if `--db` is omitted, is `skyflock.sqlite` in the working directory.

`skyflock flush` deletes sightings, flights, profiles, revisions, and promotions. `--all` also deletes aircraft snapshots and the catalog cache. The camera file and the preview are kept. The viewer has the same action at `POST /admin/flush`.

## Local viewer and commands

The viewer binds to localhost. The camera page shows the live frame, whether tracking is on, the capture pace, and the sighting count. The picture and the counters refresh in place. State vectors are a dark HTML page for a browser and JSON for an API client.

| Route | Behavior |
| --- | --- |
| `GET /` | Camera page |
| `GET /preview.jpg` | Latest frame, not cached |
| `GET /status` | Frames, spots, sightings, pace |
| `GET /settings` | Capture-rate form |
| `GET /calibrate` | Site, field of view, and sky aim |
| `GET /states`, `GET /states/all` | HTML for browsers, OpenSky JSON otherwise |
| `GET /api/states/all` | OpenSky JSON |
| `POST /admin/flush` | Delete stored sightings |

`GET /states/all` accepts `icao24`, `time`, and `lamin`, `lomin`, `lamax`, `lomax`.

| Command | Behavior |
| --- | --- |
| `setup-camera` | Open the first Windows camera that returns a frame and write a starting YAML |
| `run` | Watch that camera and serve the viewer |
| `serve` | Serve the state API with no camera |
| `import-states` | Hourly CSV or JSON, with the 15-second freshness filter |
| `export-states` | Hourly CSV and REST JSON |
| `import-aircraft` | OpenSky aircraft metadata snapshot |
| `replay` | Score a recorded detection file against an hourly state file |
| `flush` | Delete test sightings |

Import accepts a JSON list, a REST document, NDJSON, or CSV.

## Replay

`replay` compares recorded spots with an hourly state-vector file through the same camera model. The report is the number of candidates, the number matched, the association rate, and the mean angular error. It is the bench for a catalog or detector change. Video input needs OpenCV. The second-camera triangulation solver is not in this version.

## What this version leaves out

- Full Gaia, an unfiltered MPCORB, satellite TLEs, and a local readsb feed.
- OpenSky Trino, Avro archives, and separate FLARM or MLAT tables.
- A second camera and a stereo solution.
- A learned sky detector. The brightness cluster is the default, and RF-DETR is only a proposed plugin.
- Sending frames to a hosted inference API. Detection stays on the machine that holds the camera.

## Layout

```
src/skyflock/
  models/       OpenSky rows, flights, aircraft, optical extension
  opensky/      REST client and CSV/JSON import and export
  geo.py        WGS84, sidereal time, pinhole projection
  camera/       Site, mount, lens, calibration flag
  calib/        Sky mask, plate text, ADS-B pose fit, Sun/Moon/star aim
  detect/       Threshold detector, ONNX host, workload and capture pace
  track/        Greedy tracklets that close into flights
  catalogs/     Stars, OpenSky, planets, comets, asteroids
  assoc/        The cascade
  profiles/     Metadata import, revisions, promotion
  pipeline.py   One frame: gate, detect, mask, track, associate
  capture.py    Windows camera and the paced watch loop
  replay.py     Offline score
  api/          Local viewer and /states/all
  store/        SQLite
```

Tests cover the OpenSky round trip, the pose fit, the capture pace, the calibration aim, the cascade, and the local API. Run them with `python -m pytest -q -p no:cacheprovider`.
