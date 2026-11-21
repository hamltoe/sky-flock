"""Command line entry points for import, replay, and the local states API."""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path

from skyflock.api.app import serve
from skyflock.capture import load_workload_or_default, open_configured_camera, setup_camera, watch
from skyflock.detect.workload import WorkloadBox
from skyflock.opensky.io import export_hourly_csv, export_rest_json, import_states
from skyflock.profiles.service import import_aircraft_csv
from skyflock.replay import replay_files
from skyflock.store.db import Store


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="skyflock")
    parser.add_argument("--db", default="skyflock.sqlite")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_parser = sub.add_parser("serve", help="serve GET /states/all")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)

    import_states_parser = sub.add_parser("import-states", help="import an hourly CSV or JSON state file")
    import_states_parser.add_argument("path")

    export_parser = sub.add_parser("export-states", help="write the latest hour as CSV and REST JSON")
    export_parser.add_argument("csv_path")
    export_parser.add_argument("json_path")

    aircraft_parser = sub.add_parser("import-aircraft", help="import an OpenSky aircraft metadata CSV")
    aircraft_parser.add_argument("path")

    replay_parser = sub.add_parser("replay", help="score detections against an hourly state-vector file")
    replay_parser.add_argument("--states", required=True)
    replay_parser.add_argument("--detections", required=True)
    replay_parser.add_argument("--camera", required=True)

    setup_parser = sub.add_parser("setup-camera", help="open a Windows camera and write its config")
    setup_parser.add_argument("--out", default="config/camera.local.yaml")

    run_parser = sub.add_parser("run", help="watch the camera and serve the local viewer")
    run_parser.add_argument("--camera", default="config/camera.local.yaml")
    run_parser.add_argument("--workload", default=None)
    run_parser.add_argument("--preview", default="data/preview.jpg")
    run_parser.add_argument("--host", default="127.0.0.1")
    run_parser.add_argument("--port", type=int, default=8080)

    flush_parser = sub.add_parser("flush", help="delete stored sightings so a test can start clean")
    flush_parser.add_argument("--all", action="store_true", help="also delete aircraft snapshots and catalog cache")

    args = parser.parse_args(argv)
    if args.command == "setup-camera":
        camera = setup_camera(args.out)
        print(f"{camera.camera_id} {camera.width}x{camera.height} -> {args.out}")
        return
    store = Store(args.db)
    try:
        if args.command == "serve":
            serve(store, args.host, args.port)
        elif args.command == "import-states":
            count = import_states(store, args.path)
            print(count)
        elif args.command == "export-states":
            vectors = store.query_states()
            export_hourly_csv(vectors, args.csv_path)
            export_rest_json(vectors, args.json_path)
        elif args.command == "import-aircraft":
            snapshot = import_aircraft_csv(store, args.path)
            print(snapshot)
        elif args.command == "replay":
            report = replay_files(args.states, args.detections, args.camera)
            print(json.dumps(report.model_dump()))
        elif args.command == "run":
            _run_camera(args, store)
        elif args.command == "flush":
            removed = store.flush_sightings(include_reference=args.all)
            print(json.dumps(removed))
    finally:
        if args.command not in {"serve", "run"}:
            store.close()


def _run_camera(args, store: Store) -> None:
    camera = open_configured_camera(args.camera)
    workload = load_workload_or_default(args.workload)
    workload_path = args.workload or "config/workload.local.yaml"
    status: dict = {
        "frames": 0,
        "spots": 0,
        "error": None,
        "camera_id": camera.camera_id,
        "tracking": camera.ready_to_track(),
        "db": store.path,
        "preview": str(Path(args.preview)),
        "camera": args.camera,
        "camera_config": camera,
        "pace": "idle",
        "capture_fps": workload.idle_fps,
        "workload_box": WorkloadBox(workload, workload_path),
    }
    stop = threading.Event()
    worker = threading.Thread(
        target=watch,
        args=(camera, store, args.preview, stop),
        kwargs={"workload": workload, "status": status},
        daemon=True,
    )
    worker.start()
    print(f"Watching {camera.camera_id} at http://{args.host}:{args.port}/", flush=True)
    try:
        serve(store, args.host, args.port, preview_path=str(Path(args.preview)), status=status)
    finally:
        stop.set()
        worker.join(timeout=2)


if __name__ == "__main__":
    main()
