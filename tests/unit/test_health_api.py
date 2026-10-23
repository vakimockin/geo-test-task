import asyncio
from collections.abc import Sequence

import pytest
from starlette.testclient import TestClient

from geotrack.app import create_app
from geotrack.config import Settings
from geotrack.runtime import Probe
from tests.fakes import builder_for, stub_runtime


async def fine() -> None:
    return None


async def broken() -> None:
    raise RuntimeError("connection refused")


async def hangs() -> None:
    await asyncio.sleep(10)


def app_with(settings: Settings, probes: Sequence[tuple[str, Probe]]) -> TestClient:
    parts = stub_runtime(settings, probes)
    return TestClient(create_app(settings, builder=builder_for(parts)))


@pytest.fixture
def fast(settings: Settings) -> Settings:
    return settings.model_copy(update={"ready_check_timeout": 0.1})


def test_liveness(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "alive"}


def test_ready_when_every_probe_passes(fast: Settings) -> None:
    with app_with(fast, [("database", fine), ("redis", fine)]) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["probes"]["database"] == {"ok": True, "detail": None}
    assert set(body["pipeline"]) >= {"depth", "flushed", "failed", "lost"}
    assert set(body["live"]) == {"sessions", "users", "devices"}


def test_not_ready_reports_which_probe_failed(fast: Settings) -> None:
    probes = [("database", broken), ("redis", hangs), ("bus", fine)]
    with app_with(fast, probes) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["probes"]["database"]["detail"] == "connection refused"
    assert "within" in body["probes"]["redis"]["detail"]
    assert body["probes"]["bus"]["ok"] is True
