import json

from skyflock.models.common import IdentityType, Knowledge, OpticalExtension
from skyflock.models.state_vector import StateVector
from skyflock.opensky.client import OpenSkyClient, credit_cost
from skyflock.opensky.io import export_hourly_csv, export_rest_json, import_states, read_states
from skyflock.store.db import Store


def _broadcast(**overrides) -> StateVector:
    payload = dict(
        time=1_700_000_000,
        icao24="ABC123",
        lat=40.1,
        lon=-75.1,
        velocity=200.0,
        heading=90.0,
        vertrate=1.5,
        callsign="TEST1",
        onground=False,
        alert=False,
        spi=False,
        squawk="1200",
        baroaltitude=3000.0,
        geoaltitude=3100.0,
        lastposupdate=1_700_000_000,
        lastcontact=1_700_000_000,
        serials=[4, 5],
        origin_country="Test",
        position_source=0,
        category=4,
    )
    payload.update(overrides)
    return StateVector(**payload)


def test_rest_array_round_trip():
    vector = _broadcast()
    assert vector.icao24 == "abc123"
    assert vector.hour == 1_700_000_000 - (1_700_000_000 % 3600)
    restored = StateVector.from_rest_array(vector.time, vector.to_rest_array())
    assert restored.lat == vector.lat
    assert restored.heading == vector.heading
    assert restored.position_source == 0
    assert restored.category == 4
    assert restored.is_fresh()


def test_stale_vectors_are_dropped(tmp_path):
    fresh = _broadcast()
    stale = _broadcast(lastcontact=fresh.time - 40)
    path = tmp_path / "hour.json"
    path.write_text(
        json.dumps([fresh.historical_row(), stale.historical_row()]),
        encoding="utf-8",
    )
    kept = read_states(path)
    assert len(kept) == 1
    assert kept[0].lastcontact == fresh.time


def test_csv_export_and_import(tmp_path):
    vector = _broadcast()
    csv_path = tmp_path / "hour.csv"
    export_hourly_csv([vector], csv_path)
    store = Store(":memory:")
    assert import_states(store, csv_path) == 1
    loaded = store.query_states(time=vector.time)
    assert loaded[0].squawk == "1200"
    assert loaded[0].serials == [4, 5]
    json_path = tmp_path / "rest.json"
    payload = export_rest_json(loaded, json_path)
    assert isinstance(payload["states"][0], list)
    assert len(payload["states"][0]) == 18


def test_optical_rows_export_as_objects(tmp_path):
    vector = StateVector(
        time=100,
        optical=OpticalExtension(
            identity_type=IdentityType.STAR,
            knowledge=Knowledge.KNOWN,
            catalog_id="32349",
        ),
    )
    payload = export_rest_json([vector], tmp_path / "rest.json")
    assert payload["states"][0]["optical"]["catalog_id"] == "32349"


def test_store_indexes_hour_and_catalog_cache():
    store = Store(":memory:")
    vector = _broadcast()
    row_id = store.insert_state_vector(vector)
    assert store.get_state_vector(row_id).icao24 == "abc123"
    store.put_catalog_cache("hipparcos", "stars.csv", meta={"rows": 3})
    cached = store.get_catalog_cache("hipparcos")
    assert cached["path"] == "stars.csv"
    assert cached["meta"]["rows"] == 3


class _Response:
    def __init__(self, payload, headers=None):
        self._payload = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_client_uses_a_small_box_and_caches(monkeypatch):
    calls = {"n": 0}

    def opener(request, timeout=30):
        calls["n"] += 1
        assert "states/all" in request.full_url
        assert "lamin=40" in request.full_url
        return _Response(
            {
                "time": 1000,
                "states": [_broadcast(time=1000, lastposupdate=1000, lastcontact=1000).to_rest_array()],
            },
            headers={"X-Rate-Limit-Remaining": "399"},
        )

    client = OpenSkyClient(opener=opener, clock=lambda: 50)
    first = client.states_in_box(40, -76, 41, -74)
    second = client.states_in_box(40, -76, 41, -74)
    assert calls["n"] == 1
    assert first[0].icao24 == second[0].icao24 == "abc123"
    assert client.credits_remaining == 399
    assert credit_cost(40, -76, 41, -74) == 1


def test_client_refuses_a_global_box():
    client = OpenSkyClient(opener=lambda *args, **kwargs: None)
    try:
        client.states_in_box(0, 0, 30, 30)
    except ValueError as exc:
        assert "camera footprint" in str(exc)
    else:
        raise AssertionError("expected the global box to be refused")


def test_authenticated_client_fetches_a_token():
    calls = []

    def opener(request, timeout=30):
        calls.append(request.full_url)
        if "openid-connect" in request.full_url:
            return _Response({"access_token": "tok", "expires_in": 1800})
        assert request.headers["Authorization"] == "Bearer tok"
        return _Response({"time": 10, "states": []})

    client = OpenSkyClient(client_id="id", client_secret="secret", opener=opener, clock=lambda: 0)
    assert client.states_in_box(1, 2, 3, 4) == []
    assert any("openid-connect" in url for url in calls)
