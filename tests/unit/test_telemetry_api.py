from datetime import UTC, datetime, timedelta
from typing import Any

from starlette.testclient import TestClient

from tests.fakes import StubRuntimeParts


def iso(delta: timedelta = timedelta()) -> str:
    return (datetime.now(UTC) + delta).isoformat()


def report(device: str, **extra: Any) -> dict[str, Any]:
    return {"device_id": device, "latitude": 50.45, "longitude": 30.52, **extra}


def test_single_device_report(client: TestClient, parts: StubRuntimeParts) -> None:
    response = client.post(
        "/api/v1/devices/unit-1/position",
        json={"latitude": 50.45, "longitude": 30.52, "timestamp": iso()},
    )
    assert response.status_code == 202
    assert response.json() == {"queued": 1, "rejected": 0, "errors": []}
    assert parts.runtime.pipeline.depth + len(parts.flushed) >= 1


def test_single_report_validation(client: TestClient) -> None:
    url = "/api/v1/devices/unit-1/position"
    assert client.post(url, json={"latitude": 91, "longitude": 0}).status_code == 422
    stale = {"latitude": 1, "longitude": 1, "timestamp": iso(-timedelta(days=2))}
    assert client.post(url, json=stale).status_code == 422
    bad_id = "/api/v1/devices/" + "x" * 129 + "/position"
    assert client.post(bad_id, json={"latitude": 1, "longitude": 1}).status_code == 422


def test_batch_partial_accept(client: TestClient) -> None:
    response = client.post(
        "/api/v1/positions",
        json={
            "positions": [
                report("a", timestamp=iso()),
                report("b", timestamp=iso(timedelta(minutes=10))),
                report("c", timestamp=iso(-timedelta(days=2))),
            ]
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert (body["queued"], body["rejected"]) == (1, 2)
    assert [e["index"] for e in body["errors"]] == [1, 2]


def test_batch_schema_errors(client: TestClient) -> None:
    payloads: list[dict[str, Any]] = [
        {"positions": []},
        {"positions": [{"latitude": 1, "longitude": 1}]},
        {"positions": [report("x" * 129)]},
        {"positions": [report("a", latitude=91)]},
        {"items": [report("a")]},
    ]
    for payload in payloads:
        assert client.post("/api/v1/positions", json=payload).status_code == 422
    raw = client.post(
        "/api/v1/positions", content=b"{", headers={"Content-Type": "application/json"}
    )
    assert raw.status_code == 422


def test_batch_with_only_bad_timestamps_is_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/positions",
        json={"positions": [report("a", timestamp=iso(timedelta(hours=1)))]},
    )
    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == "body.positions.0.timestamp"


def test_full_queue_answers_503_with_retry_after(client: TestClient) -> None:
    # The stub pipeline is never started, so nothing drains (capacity 50).
    batch = {"positions": [report(f"d{i}") for i in range(60)]}
    first = client.post("/api/v1/positions", json=batch).json()
    assert (first["queued"], first["rejected"]) == (50, 10)
    assert {e["reason"] for e in first["errors"]} == {"overloaded, retry later"}
    response = client.post("/api/v1/positions", json=batch)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json()["type"] == "urn:geotrack:problem:overloaded"
