from typing import Any

import pytest
from starlette.testclient import TestClient

ALICE = {"X-User-ID": "alice"}
BOB = {"X-User-ID": "bob"}
BODY = {"name": "office", "latitude": 50.45, "longitude": 30.52, "radius_m": 500}


def create(client: TestClient, body: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post("/api/v1/geozones", json=body or BODY, headers=ALICE)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def test_create_and_read(client: TestClient) -> None:
    zone = create(client)
    assert {k: zone[k] for k in BODY} == BODY
    got = client.get(f"/api/v1/geozones/{zone['id']}", headers=ALICE)
    assert got.status_code == 200
    assert got.json() == zone


def test_list_only_shows_own_zones(client: TestClient) -> None:
    mine = create(client)
    client.post("/api/v1/geozones", json=BODY, headers=BOB)
    listed = client.get("/api/v1/geozones", headers=ALICE).json()
    assert [z["id"] for z in listed] == [mine["id"]]


def test_list_paging_is_validated(client: TestClient) -> None:
    assert client.get("/api/v1/geozones?limit=0", headers=ALICE).status_code == 422
    assert client.get("/api/v1/geozones?offset=-1", headers=ALICE).status_code == 422


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_foreign_zone_is_404(client: TestClient, method: str) -> None:
    zone = create(client)
    kwargs: dict[str, Any] = {"headers": BOB}
    if method in ("put", "patch"):
        kwargs["json"] = BODY
    response = getattr(client, method)(f"/api/v1/geozones/{zone['id']}", **kwargs)
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


def test_put_requires_the_whole_zone(client: TestClient) -> None:
    zone = create(client)
    url = f"/api/v1/geozones/{zone['id']}"
    partial = {"latitude": 1, "longitude": 2}
    assert client.put(url, json=partial, headers=ALICE).status_code == 422
    replaced = client.put(
        url, json={"latitude": 1, "longitude": 2, "radius_m": 30}, headers=ALICE
    ).json()
    assert (replaced["name"], replaced["radius_m"]) == ("", 30)


def test_patch_changes_only_given_fields(client: TestClient) -> None:
    zone = create(client)
    url = f"/api/v1/geozones/{zone['id']}"
    patched = client.patch(url, json={"radius_m": 800}, headers=ALICE).json()
    assert (patched["name"], patched["latitude"], patched["radius_m"]) == (
        "office",
        50.45,
        800,
    )
    assert client.patch(url, json={}, headers=ALICE).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"latitude": 1.0},
        {"radius_m": None},
        {"name": "x" * 101},
        {"colour": "red"},
    ],
)
def test_patch_rejects(client: TestClient, body: dict[str, Any]) -> None:
    zone = create(client)
    response = client.patch(f"/api/v1/geozones/{zone['id']}", json=body, headers=ALICE)
    assert response.status_code == 422


def test_delete(client: TestClient) -> None:
    zone = create(client)
    url = f"/api/v1/geozones/{zone['id']}"
    assert client.delete(url, headers=ALICE).status_code == 204
    assert client.get(url, headers=ALICE).status_code == 404
    assert client.delete(url, headers=ALICE).status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        {"latitude": 90.1},
        {"longitude": -180.1},
        {"radius_m": 0},
        {"radius_m": -5},
        {"radius_m": 100_001},
        {"name": "n" * 101},
    ],
)
def test_create_rejects_out_of_range(
    client: TestClient, change: dict[str, Any]
) -> None:
    response = client.post("/api/v1/geozones", json={**BODY, **change}, headers=ALICE)
    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == "urn:geotrack:problem:invalid-input"
    assert problem["errors"][0]["loc"].startswith("body.")


def test_limits_are_inclusive(client: TestClient) -> None:
    create(client, {**BODY, "radius_m": 100_000, "latitude": 90, "longitude": -180})


def test_nan_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/geozones",
        content=b'{"latitude": NaN, "longitude": 1, "radius_m": 5}',
        headers={**ALICE, "Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_quota_conflict(client: TestClient) -> None:
    for _ in range(3):
        create(client)
    response = client.post("/api/v1/geozones", json=BODY, headers=ALICE)
    assert response.status_code == 409
    assert client.post("/api/v1/geozones", json=BODY, headers=BOB).status_code == 201


def test_malformed_id_is_422(client: TestClient) -> None:
    assert client.get("/api/v1/geozones/not-a-uuid", headers=ALICE).status_code == 422
