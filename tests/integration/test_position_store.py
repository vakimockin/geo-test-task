import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from geotrack.telemetry.model import PositionReport
from geotrack.telemetry.store import INGEST_PARAMS, INGEST_QUERY, PositionStore

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 23, 7, 30, tzinfo=UTC)


async def add_zone(
    engine: AsyncEngine, owner: str, lat: float, lon: float, radius_m: float
) -> uuid.UUID:
    zone_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO geozones (id, owner_id, name, lat, lon, radius_m, "
                "created_at, updated_at) "
                "VALUES (:id, :owner, '', :lat, :lon, :radius, now(), now())"
            ),
            {"id": zone_id, "owner": owner, "lat": lat, "lon": lon, "radius": radius_m},
        )
    return zone_id


async def project(
    engine: AsyncEngine, lat: float, lon: float, meters: float, bearing_deg: float
) -> tuple[float, float]:
    """Point at a geodesic distance and bearing, computed by PostGIS."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT ST_Y(p::geometry), ST_X(p::geometry) FROM ("
                    " SELECT ST_Project(ST_Point(:lon, :lat, 4326)::geography,"
                    " CAST(:meters AS float8), radians(:bearing)) AS p) AS q"
                ),
                {"lat": lat, "lon": lon, "meters": meters, "bearing": bearing_deg},
            )
        ).one()
    return row[0], row[1]


async def test_stores_latest_and_reports_hits_per_owner(engine: AsyncEngine) -> None:
    alice = await add_zone(engine, "alice", 50.45, 30.52, 1_000)
    bob = await add_zone(engine, "bob", 50.45, 30.52, 300)
    await add_zone(engine, "carol", 10.0, 10.0, 1_000)
    store = PositionStore(engine)

    outcome = await store.apply(
        [
            PositionReport("near", 50.4505, 30.5205, NOW),
            PositionReport("mid", 50.454, 30.52, NOW),
            PositionReport("far", 51.0, 31.0, NOW),
        ]
    )

    assert sorted(p.device_id for p in outcome.applied) == ["far", "mid", "near"]
    hits = sorted((h.device_id, h.owner_id, h.geozone_id) for h in outcome.hits)
    assert hits == [
        ("mid", "alice", alice),
        ("near", "alice", alice),
        ("near", "bob", bob),
    ]


async def test_older_reports_change_nothing(engine: AsyncEngine) -> None:
    await add_zone(engine, "alice", 50.45, 30.52, 1_000)
    store = PositionStore(engine)
    await store.apply([PositionReport("d", 1.0, 1.0, NOW)])

    stale = await store.apply(
        [PositionReport("d", 50.45, 30.52, NOW - timedelta(seconds=5))]
    )
    same = await store.apply([PositionReport("d", 50.45, 30.52, NOW)])

    assert stale.applied == [] and stale.hits == []
    assert same.applied == []
    recent = await store.recent(timedelta(minutes=5))
    assert [(p.lat, p.lon) for p in recent] == [(1.0, 1.0)]


async def test_newest_report_in_a_batch_wins(engine: AsyncEngine) -> None:
    store = PositionStore(engine)
    outcome = await store.apply(
        [
            PositionReport("d", 2.0, 2.0, NOW + timedelta(seconds=2)),
            PositionReport("d", 1.0, 1.0, NOW),
            PositionReport("d", 3.0, 3.0, NOW + timedelta(seconds=1)),
        ]
    )
    assert [(p.lat, p.reported_at) for p in outcome.applied] == [
        (2.0, NOW + timedelta(seconds=2))
    ]


CENTERS = [
    pytest.param(0.0, 0.0, id="equator"),
    pytest.param(50.45, 30.52, id="kyiv"),
    pytest.param(-33.87, 151.21, id="sydney"),
    pytest.param(78.22, 15.65, id="svalbard"),
    pytest.param(0.0, 179.995, id="antimeridian"),
    pytest.param(89.6, 45.0, id="near-pole"),
]


@pytest.mark.parametrize("lat, lon", CENTERS)
@pytest.mark.parametrize("radius_m", [250.0, 40_000.0])
async def test_circle_boundary_is_exact_in_every_direction(
    engine: AsyncEngine, lat: float, lon: float, radius_m: float
) -> None:
    zone_id = await add_zone(engine, "alice", lat, lon, radius_m)
    reports: list[PositionReport] = []
    expected_inside: set[str] = set()
    for bearing in range(0, 360, 30):
        for label, factor in (("in", 0.995), ("out", 1.005)):
            plat, plon = await project(engine, lat, lon, radius_m * factor, bearing)
            device = f"{label}-{bearing}"
            reports.append(PositionReport(device, plat, plon, NOW))
            if label == "in":
                expected_inside.add(device)

    outcome = await PositionStore(engine).apply(reports)

    inside = {h.device_id for h in outcome.hits if h.geozone_id == zone_id}
    assert inside == expected_inside


async def test_zone_lookup_is_served_by_the_envelope_index(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO geozones (id, owner_id, name, lat, lon, radius_m, "
                "created_at, updated_at) "
                "SELECT gen_random_uuid(), 'u' || (g % 50), '', "
                "       -60 + (g * 7.3) % 120, -170 + (g * 13.1) % 340, 500, "
                "       now(), now() "
                "FROM generate_series(1, 20000) AS g"
            )
        )
        await conn.execute(text("ANALYZE geozones"))
    explain = text("EXPLAIN " + INGEST_QUERY).bindparams(*INGEST_PARAMS)
    params = {
        "device_ids": [f"d{i}" for i in range(50)],
        "lats": [50.0 + i / 1000 for i in range(50)],
        "lons": [30.0] * 50,
        "reported_ats": [NOW] * 50,
    }
    async with engine.connect() as conn:
        plan = "\n".join((await conn.execute(explain, params)).scalars())
    assert "ix_geozones_envelope" in plan
    assert "Seq Scan on geozones" not in plan
