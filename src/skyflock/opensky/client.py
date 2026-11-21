"""Live OpenSky REST client. Requests stay inside a local bounding box."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from skyflock.models.state_vector import StateVector

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
API_ROOT = "https://opensky-network.org/api"
TOKEN_REFRESH_MARGIN_S = 30
MAX_BOX_SQ_DEG = 400.0


def bbox_area(lamin: float, lomin: float, lamax: float, lomax: float) -> float:
    return abs(lamax - lamin) * abs(lomax - lomin)


def credit_cost(lamin: float, lomin: float, lamax: float, lomax: float) -> int:
    """Credit cost of `GET /states/all` for this box, from the OpenSky rate table."""
    area = bbox_area(lamin, lomin, lamax, lomax)
    if area <= 25:
        return 1
    if area <= 100:
        return 2
    if area <= 400:
        return 3
    return 4


class OpenSkyClient:
    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        opener: Callable[..., Any] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.opener = opener or urllib.request.urlopen
        self.clock = clock or time.time
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._cache_key: tuple[float, float, float, float] | None = None
        self._cache_at: float = 0.0
        self._cache: list[StateVector] = []
        self.credits_remaining: int | None = None

    @property
    def authenticated(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def states_in_box(self, lamin: float, lomin: float, lamax: float, lomax: float) -> list[StateVector]:
        area = bbox_area(lamin, lomin, lamax, lomax)
        if area <= 0:
            raise ValueError("bounding box is empty")
        if area > MAX_BOX_SQ_DEG:
            raise ValueError(
                "bounding box is too large; request the camera footprint, not the global feed"
            )
        now = self.clock()
        resolution = 5.0 if self.authenticated else 10.0
        key = (lamin, lomin, lamax, lomax)
        if self._cache_key == key and (now - self._cache_at) < resolution:
            return list(self._cache)
        payload = self._get_states(lamin, lomin, lamax, lomax)
        vectors = [StateVector.from_rest_array(int(payload["time"]), row) for row in payload.get("states") or []]
        fresh = [vector for vector in vectors if vector.is_fresh()]
        self._cache_key = key
        self._cache_at = now
        self._cache = fresh
        return list(fresh)

    def _get_states(self, lamin: float, lomin: float, lamax: float, lomax: float, *, retry: bool = True) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {
                "lamin": lamin,
                "lomin": lomin,
                "lamax": lamax,
                "lomax": lomax,
                "extended": 1,
            }
        )
        request = urllib.request.Request(f"{API_ROOT}/states/all?{query}")
        if self.authenticated:
            request.add_header("Authorization", f"Bearer {self._access_token()}")
        try:
            with self.opener(request, timeout=30) as response:
                self._read_credit_header(response)
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and self.authenticated and retry:
                self._token = None
                return self._get_states(lamin, lomin, lamax, lomax, retry=False)
            raise

    def _access_token(self) -> str:
        now = self.clock()
        if self._token and now < self._expires_at:
            return self._token
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with self.opener(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self._token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 1800))
        self._expires_at = now + expires_in - TOKEN_REFRESH_MARGIN_S
        return self._token

    def _read_credit_header(self, response: Any) -> None:
        headers = getattr(response, "headers", None)
        if headers is None:
            return
        remaining = headers.get("X-Rate-Limit-Remaining")
        if remaining is not None and str(remaining).isdigit():
            self.credits_remaining = int(remaining)
