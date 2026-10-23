from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.testclient import TestClient, WebSocketTestSession

from geotrack.app import create_app
from geotrack.config import Settings
from tests.timeouts import receive_frame

pytestmark = pytest.mark.integration


@pytest.fixture
def client(
    db_settings: Settings, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    # fakeredis cannot answer redis-py health checks, so connection options are dropped
    monkeypatch.setattr(
        Redis,
        "from_url",
        staticmethod(lambda *a, **k: FakeAsyncRedis(decode_responses=True)),
    )
    with TestClient(create_app(db_settings)) as test_client:
        yield test_client


def frame_of_kind(
    ws: WebSocketTestSession, kind: str, limit: int = 30
) -> dict[str, Any]:
    for _ in range(limit):
        frame = receive_frame(ws)
        if frame["kind"] == kind:
            return frame
    raise AssertionError(f"no {kind!r} frame received")


def wait_ready(client: TestClient) -> dict[str, Any]:
    for _ in range(100):
        response = client.get("/health/ready")
        if response.status_code == 200:
            ready: dict[str, Any] = response.json()
            return ready
    raise AssertionError(client.get("/health/ready").json())


def test_reports_reach_every_session_of_the_zone_owner(client: TestClient) -> None:
    wait_ready(client)
    created = client.post(
        "/api/v1/geozones",
        json={
            "name": "center",
            "latitude": 50.45,
            "longitude": 30.52,
            "radius_m": 1_000,
        },
        headers={"X-User-ID": "alice"},
    )
    assert created.status_code == 201
    zone_id = created.json()["id"]
    client.post(
        "/api/v1/geozones",
        json={"latitude": 10.0, "longitude": 10.0, "radius_m": 1_000},
        headers={"X-User-ID": "bob"},
    )

    with (
        client.websocket_connect("/ws/live?user=alice") as laptop,
        client.websocket_connect("/ws/live?user=alice") as phone,
        client.websocket_connect("/ws/live?user=bob") as other,
    ):
        for ws in (laptop, phone, other):
            frame_of_kind(ws, "hello")
        while wait_ready(client)["live"]["sessions"] < 3:
            pass

        accepted = client.post(
            "/api/v1/positions",
            json={
                "positions": [
                    {
                        "device_id": "unit-7",
                        "latitude": 50.4505,
                        "longitude": 30.5205,
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                ]
            },
        )
        assert accepted.json()["queued"] == 1

        for ws in (laptop, phone):
            alert = frame_of_kind(ws, "alert")
            assert alert["items"][0]["geozone_id"] == zone_id
            assert alert["items"][0]["device_id"] == "unit-7"
        positions = frame_of_kind(other, "positions")
        assert positions["items"][0]["device_id"] == "unit-7"

        client.delete(f"/api/v1/geozones/{zone_id}", headers={"X-User-ID": "alice"})
        for ws in (laptop, phone):
            changed = frame_of_kind(ws, "geozones_changed")
            assert changed == {
                "kind": "geozones_changed",
                "action": "deleted",
                "id": zone_id,
            }

    ready = wait_ready(client)
    assert ready["pipeline"]["flushed"] == 1
    assert ready["probes"]["database"]["ok"] and ready["probes"]["bus"]["ok"]
