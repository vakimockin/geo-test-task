import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from geotrack.db.engine import make_sessions
from geotrack.db.tables import Geozone
from geotrack.zones.repository import SqlGeozoneRepository

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 23, 7, tzinfo=UTC)


def zone(owner: str, *, radius: float = 500, at: datetime = T0) -> Geozone:
    return Geozone(
        id=uuid.uuid4(),
        owner_id=owner,
        name=f"{owner}-zone",
        latitude=50.45,
        longitude=30.52,
        radius_m=radius,
        created_at=at,
        updated_at=at,
    )


async def test_round_trip_and_owner_scoping(engine: AsyncEngine) -> None:
    repo = SqlGeozoneRepository(make_sessions(engine))
    mine = zone("alice")
    assert await repo.insert(mine, limit=10)
    found = await repo.find("alice", mine.id)
    assert found is not None
    assert (found.name, found.latitude, found.longitude, found.radius_m) == (
        "alice-zone",
        50.45,
        30.52,
        500,
    )
    assert await repo.find("bob", mine.id) is None
    assert await repo.change("bob", mine.id, {"radius_m": 1}) is None
    assert not await repo.remove("bob", mine.id)
    assert await repo.remove("alice", mine.id)
    assert await repo.find("alice", mine.id) is None


async def test_page_orders_by_creation(engine: AsyncEngine) -> None:
    repo = SqlGeozoneRepository(make_sessions(engine))
    zones = [zone("alice", at=T0 + timedelta(minutes=i)) for i in range(5)]
    for item in reversed(zones):
        await repo.insert(item, limit=10)
    page = await repo.page("alice", limit=2, offset=1)
    assert [z.id for z in page] == [zones[1].id, zones[2].id]


async def test_generated_columns_follow_the_numbers(engine: AsyncEngine) -> None:
    repo = SqlGeozoneRepository(make_sessions(engine))
    item = zone("alice", radius=1_000)
    await repo.insert(item, limit=10)

    async def shape() -> tuple[str, float]:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT ST_AsText(center), "
                        "ST_YMax(envelope) - ST_YMin(envelope) "
                        "FROM geozones WHERE id = :id"
                    ),
                    {"id": item.id},
                )
            ).one()
        return row[0], row[1]

    center, height = await shape()
    assert center == "POINT(30.52 50.45)"
    changed = await repo.change(
        "alice", item.id, {"radius_m": 10_000, "latitude": 10.0, "longitude": 20.0}
    )
    assert changed is not None and changed.radius_m == 10_000
    moved_center, moved_height = await shape()
    assert moved_center == "POINT(20 10)"
    assert moved_height == pytest.approx(height * 10, rel=1e-6)


async def test_quota_holds_under_concurrent_creates(engine: AsyncEngine) -> None:
    repo = SqlGeozoneRepository(make_sessions(engine))
    results = await asyncio.gather(
        *(repo.insert(zone("alice"), limit=3) for _ in range(10))
    )
    assert sum(results) == 3
    assert len(await repo.page("alice", limit=50, offset=0)) == 3


async def test_database_rejects_out_of_range_radius(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        with pytest.raises(Exception, match="ck_geozones_radius_range"):
            await conn.execute(
                text(
                    "INSERT INTO geozones (id, owner_id, name, lat, lon, radius_m, "
                    "created_at, updated_at) VALUES (gen_random_uuid(), 'a', '', 0, 0,"
                    " 200000, now(), now())"
                )
            )
