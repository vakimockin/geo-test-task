from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from geotrack.security import parse_user_id


@pytest.mark.parametrize("raw", ["alice", "user.1@corp", "a_b-c", "x" * 64])
def test_valid_user_ids(raw: str) -> None:
    assert parse_user_id(raw) == raw


@pytest.mark.parametrize("raw", [None, "", "   ", "a b", "x*", "x" * 65, "ž"])
def test_invalid_user_ids(raw: str | None) -> None:
    assert parse_user_id(raw) is None


@pytest.mark.parametrize("header", [None, "", "a b", "x" * 65])
def test_missing_or_bad_user_header_is_401(
    client: TestClient, header: str | None
) -> None:
    headers = {} if header is None else {"X-User-ID": header}
    response = client.get("/api/v1/geozones", headers=headers)
    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert set(body) == {"type", "title", "status", "detail"}
    assert body["status"] == 401


def test_unknown_route_and_method_use_problem_details(client: TestClient) -> None:
    missing = client.get("/nope")
    assert missing.status_code == 404
    assert missing.json()["title"] == "Not Found"
    wrong = client.put("/api/v1/geozones")
    assert wrong.status_code == 405
    assert wrong.headers["content-type"] == "application/problem+json"


def test_declared_oversized_body_is_413(client: TestClient) -> None:
    response = client.post(
        "/api/v1/geozones",
        content=b"x" * 200_001,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["type"] == "urn:geotrack:problem:payload-too-large"


def test_streamed_oversized_body_is_413(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(30):
            yield b"x" * 10_000

    response = client.post(
        "/api/v1/geozones",
        content=chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_openapi_lists_the_api(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/api/v1/geozones"} <= set(paths)
