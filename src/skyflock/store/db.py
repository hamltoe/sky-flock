"""SQLite store. State vectors are append-only. Aircraft snapshots are immutable."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from skyflock.models.aircraft import AircraftRecord
from skyflock.models.common import IdentityType, Knowledge, KnowledgeSource, identity_key
from skyflock.models.flight import Flight
from skyflock.models.state_vector import StateVector

SCHEMA = """
CREATE TABLE IF NOT EXISTS state_vectors (
    id INTEGER PRIMARY KEY,
    time INTEGER NOT NULL,
    hour INTEGER NOT NULL,
    icao24 TEXT,
    local_id TEXT,
    catalog_id TEXT,
    identity_type TEXT,
    knowledge TEXT,
    knowledge_source TEXT,
    lat REAL,
    lon REAL,
    velocity REAL,
    heading REAL,
    vertrate REAL,
    callsign TEXT,
    onground INTEGER,
    alert INTEGER,
    spi INTEGER,
    squawk TEXT,
    baroaltitude REAL,
    geoaltitude REAL,
    lastposupdate REAL,
    lastcontact REAL,
    serials TEXT,
    origin_country TEXT,
    position_source INTEGER,
    category INTEGER,
    optical TEXT,
    profile_id INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sv_hour ON state_vectors(hour);
CREATE INDEX IF NOT EXISTS idx_sv_icao ON state_vectors(icao24);
CREATE INDEX IF NOT EXISTS idx_sv_local ON state_vectors(local_id);
CREATE INDEX IF NOT EXISTS idx_sv_catalog ON state_vectors(catalog_id);
CREATE INDEX IF NOT EXISTS idx_sv_time ON state_vectors(time);

CREATE TABLE IF NOT EXISTS flights (
    id INTEGER PRIMARY KEY,
    icao24 TEXT,
    local_id TEXT,
    catalog_id TEXT,
    identity_type TEXT,
    firstseen INTEGER,
    lastseen INTEGER NOT NULL,
    callsign TEXT,
    estdepartureairport TEXT,
    estarrivalairport TEXT,
    track TEXT NOT NULL,
    day INTEGER NOT NULL,
    profile_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_flights_day ON flights(day);

CREATE TABLE IF NOT EXISTS aircraft_snapshots (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    imported_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS aircraft (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL,
    icao24 TEXT NOT NULL,
    registration TEXT,
    manufacturericao TEXT,
    manufacturername TEXT,
    model TEXT,
    typecode TEXT,
    serialnumber TEXT,
    operator TEXT,
    operatoricao TEXT,
    owner TEXT,
    built TEXT,
    reguntil TEXT,
    extra TEXT,
    UNIQUE(snapshot_id, icao24)
);

CREATE TABLE IF NOT EXISTS catalog_cache (
    name TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    fetched_at INTEGER,
    meta TEXT
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY,
    identity_key TEXT NOT NULL UNIQUE,
    identity_type TEXT NOT NULL,
    icao24 TEXT,
    catalog_id TEXT,
    local_id TEXT,
    knowledge TEXT NOT NULL,
    knowledge_source TEXT,
    size_px REAL,
    shape TEXT,
    color TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_revisions (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL,
    valid_from INTEGER NOT NULL,
    class_label TEXT,
    notes TEXT,
    embedding_id TEXT,
    size_px REAL,
    shape TEXT,
    color TEXT
);

CREATE TABLE IF NOT EXISTS profile_promotions (
    id INTEGER PRIMARY KEY,
    from_profile_id INTEGER NOT NULL,
    to_profile_id INTEGER NOT NULL,
    at_time INTEGER NOT NULL,
    note TEXT
);
"""


def _b(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _flag(value: int | None) -> bool | None:
    if value is None:
        return None
    return bool(value)


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=5000")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def flush_sightings(self, *, include_reference: bool = False) -> dict[str, int]:
        """Delete stored observations. Camera files on disk are left in place."""
        tables = [
            "state_vectors",
            "flights",
            "profile_promotions",
            "profile_revisions",
            "profiles",
        ]
        if include_reference:
            tables.extend(["aircraft", "aircraft_snapshots", "catalog_cache"])
        counts: dict[str, int] = {}
        for table in tables:
            counts[table] = int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()
        return counts

    def insert_state_vector(self, vector: StateVector, profile_id: int | None = None) -> int:
        optical = vector.optical
        now = int(time.time())
        cur = self.conn.execute(
            """
            INSERT INTO state_vectors (
                time, hour, icao24, local_id, catalog_id, identity_type, knowledge,
                knowledge_source, lat, lon, velocity, heading, vertrate, callsign,
                onground, alert, spi, squawk, baroaltitude, geoaltitude, lastposupdate,
                lastcontact, serials, origin_country, position_source, category, optical,
                profile_id, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                vector.time,
                vector.hour,
                vector.icao24,
                optical.local_id if optical else None,
                optical.catalog_id if optical else None,
                optical.identity_type.value if optical else None,
                optical.knowledge.value if optical else None,
                optical.knowledge_source.value if optical and optical.knowledge_source else None,
                vector.lat,
                vector.lon,
                vector.velocity,
                vector.heading,
                vector.vertrate,
                vector.callsign,
                _b(vector.onground),
                _b(vector.alert),
                _b(vector.spi),
                vector.squawk,
                vector.baroaltitude,
                vector.geoaltitude,
                vector.lastposupdate,
                vector.lastcontact,
                json.dumps(vector.serials) if vector.serials is not None else None,
                vector.origin_country,
                vector.position_source,
                vector.category,
                optical.model_dump_json() if optical else None,
                profile_id,
                now,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def state_vector_row(self, row_id: int) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM state_vectors WHERE id = ?", (row_id,)).fetchone()
        if row is None:
            raise KeyError(row_id)
        return row

    def get_state_vector(self, row_id: int) -> StateVector:
        return _vector_from_row(self.state_vector_row(row_id))

    def query_states(
        self,
        *,
        time: int | None = None,
        icao24: str | None = None,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> list[StateVector]:
        clauses: list[str] = []
        params: list[Any] = []
        if time is None:
            latest = self.conn.execute("SELECT MAX(time) AS t FROM state_vectors").fetchone()
            time = None if latest is None or latest["t"] is None else int(latest["t"])
        if time is not None:
            clauses.append("time = ?")
            params.append(time)
        if icao24:
            clauses.append("icao24 = ?")
            params.append(icao24.strip().lower())
        if bbox is not None:
            lamin, lomin, lamax, lomax = bbox
            clauses.append("lat IS NOT NULL AND lon IS NOT NULL AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?")
            params.extend([lamin, lamax, lomin, lomax])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM state_vectors {where} ORDER BY id",
            params,
        ).fetchall()
        return [_vector_from_row(row) for row in rows]

    def insert_flight(self, flight: Flight, profile_id: int | None = None) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO flights (
                icao24, local_id, catalog_id, identity_type, firstseen, lastseen, callsign,
                estdepartureairport, estarrivalairport, track, day, profile_id
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                flight.icao24,
                flight.local_id,
                flight.catalog_id,
                flight.identity_type,
                flight.firstseen,
                flight.lastseen,
                flight.callsign,
                flight.estdepartureairport,
                flight.estarrivalairport,
                json.dumps([point.model_dump() for point in flight.track]),
                flight.day,
                profile_id,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def add_aircraft_snapshot(self, source: str, records: list[AircraftRecord], imported_at: int | None = None) -> int:
        stamp = int(time.time()) if imported_at is None else imported_at
        cur = self.conn.execute(
            "INSERT INTO aircraft_snapshots (source, imported_at) VALUES (?, ?)",
            (source, stamp),
        )
        snapshot_id = int(cur.lastrowid)
        for record in records:
            extra = {key: value for key, value in record.extra.items()}
            self.conn.execute(
                """
                INSERT INTO aircraft (
                    snapshot_id, icao24, registration, manufacturericao, manufacturername,
                    model, typecode, serialnumber, operator, operatoricao, owner, built,
                    reguntil, extra
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    snapshot_id,
                    record.icao24,
                    record.registration,
                    record.manufacturericao,
                    record.manufacturername,
                    record.model,
                    record.typecode,
                    record.serialnumber,
                    record.operator,
                    record.operatoricao,
                    record.owner,
                    record.built,
                    record.reguntil,
                    json.dumps(extra),
                ),
            )
        self.conn.commit()
        return snapshot_id

    def get_aircraft(self, icao24: str, snapshot_id: int | None = None) -> AircraftRecord | None:
        key = icao24.strip().lower()
        if snapshot_id is None:
            row = self.conn.execute(
                """
                SELECT * FROM aircraft
                WHERE icao24 = ?
                ORDER BY snapshot_id DESC
                LIMIT 1
                """,
                (key,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM aircraft WHERE icao24 = ? AND snapshot_id = ?",
                (key, snapshot_id),
            ).fetchone()
        if row is None:
            return None
        return AircraftRecord(
            icao24=row["icao24"],
            registration=row["registration"],
            manufacturericao=row["manufacturericao"],
            manufacturername=row["manufacturername"],
            model=row["model"],
            typecode=row["typecode"],
            serialnumber=row["serialnumber"],
            operator=row["operator"],
            operatoricao=row["operatoricao"],
            owner=row["owner"],
            built=row["built"],
            reguntil=row["reguntil"],
            extra=json.loads(row["extra"] or "{}"),
            snapshot_id=row["snapshot_id"],
        )

    def put_catalog_cache(self, name: str, path: str, *, fetched_at: int | None = None, meta: dict | None = None) -> None:
        stamp = int(time.time()) if fetched_at is None else fetched_at
        self.conn.execute(
            """
            INSERT INTO catalog_cache (name, path, fetched_at, meta)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET path = excluded.path, fetched_at = excluded.fetched_at, meta = excluded.meta
            """,
            (name, path, stamp, json.dumps(meta or {})),
        )
        self.conn.commit()

    def get_catalog_cache(self, name: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM catalog_cache WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        return {
            "name": row["name"],
            "path": row["path"],
            "fetched_at": row["fetched_at"],
            "meta": json.loads(row["meta"] or "{}"),
        }

    def upsert_profile(
        self,
        *,
        identity_type: IdentityType | str,
        knowledge: Knowledge | str,
        knowledge_source: KnowledgeSource | str | None = None,
        icao24: str | None = None,
        catalog_id: str | None = None,
        local_id: str | None = None,
        size_px: float | None = None,
        shape: str | None = None,
        color: str | None = None,
    ) -> int:
        kind = IdentityType(identity_type)
        known = Knowledge(knowledge)
        source = KnowledgeSource(knowledge_source) if knowledge_source else None
        key = identity_key(kind, icao24=icao24, catalog_id=catalog_id, local_id=local_id)
        existing = self.conn.execute("SELECT id FROM profiles WHERE identity_key = ?", (key,)).fetchone()
        if existing:
            self.conn.execute(
                """
                UPDATE profiles
                SET knowledge = ?, knowledge_source = ?,
                    size_px = COALESCE(?, size_px),
                    shape = COALESCE(?, shape),
                    color = COALESCE(?, color)
                WHERE id = ?
                """,
                (
                    known.value,
                    source.value if source else None,
                    size_px,
                    shape,
                    color,
                    existing["id"],
                ),
            )
            self.conn.commit()
            return int(existing["id"])
        cur = self.conn.execute(
            """
            INSERT INTO profiles (
                identity_key, identity_type, icao24, catalog_id, local_id, knowledge,
                knowledge_source, size_px, shape, color, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                key,
                kind.value,
                icao24,
                catalog_id,
                local_id,
                known.value,
                source.value if source else None,
                size_px,
                shape,
                color,
                int(time.time()),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def add_revision(
        self,
        profile_id: int,
        valid_from: int,
        *,
        class_label: str | None = None,
        notes: str | None = None,
        embedding_id: str | None = None,
        size_px: float | None = None,
        shape: str | None = None,
        color: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO profile_revisions (
                profile_id, valid_from, class_label, notes, embedding_id, size_px, shape, color
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (profile_id, valid_from, class_label, notes, embedding_id, size_px, shape, color),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def revisions_for(self, profile_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM profile_revisions WHERE profile_id = ? ORDER BY valid_from, id",
            (profile_id,),
        ).fetchall()

    def promote(
        self,
        local_id: str,
        *,
        identity_type: IdentityType | str,
        at_time: int,
        catalog_id: str | None = None,
        icao24: str | None = None,
        knowledge_source: KnowledgeSource | str | None = None,
        note: str | None = None,
    ) -> int:
        """Point measurements at and after `at_time` at the catalog profile.

        Measurement columns on those rows stay as they were written.
        """
        source = self.conn.execute(
            "SELECT id FROM profiles WHERE local_id = ? AND identity_type = ?",
            (local_id, IdentityType.LOCAL.value),
        ).fetchone()
        if source is None:
            raise KeyError(f"no local profile for {local_id}")
        target = self.upsert_profile(
            identity_type=identity_type,
            knowledge=Knowledge.KNOWN,
            knowledge_source=knowledge_source,
            icao24=icao24,
            catalog_id=catalog_id,
            local_id=None,
        )
        self.conn.execute(
            """
            INSERT INTO profile_promotions (from_profile_id, to_profile_id, at_time, note)
            VALUES (?, ?, ?, ?)
            """,
            (source["id"], target, at_time, note),
        )
        self.conn.execute(
            """
            UPDATE state_vectors
            SET profile_id = ?
            WHERE local_id = ? AND time >= ?
            """,
            (target, local_id, at_time),
        )
        self.conn.commit()
        return target


def _vector_from_row(row: sqlite3.Row) -> StateVector:
    optical = json.loads(row["optical"]) if row["optical"] else None
    serials = json.loads(row["serials"]) if row["serials"] else None
    return StateVector.model_validate(
        {
            "time": row["time"],
            "hour": row["hour"],
            "icao24": row["icao24"],
            "lat": row["lat"],
            "lon": row["lon"],
            "velocity": row["velocity"],
            "heading": row["heading"],
            "vertrate": row["vertrate"],
            "callsign": row["callsign"],
            "onground": _flag(row["onground"]),
            "alert": _flag(row["alert"]),
            "spi": _flag(row["spi"]),
            "squawk": row["squawk"],
            "baroaltitude": row["baroaltitude"],
            "geoaltitude": row["geoaltitude"],
            "lastposupdate": row["lastposupdate"],
            "lastcontact": row["lastcontact"],
            "serials": serials,
            "origin_country": row["origin_country"],
            "position_source": row["position_source"],
            "category": row["category"],
            "optical": optical,
        }
    )
