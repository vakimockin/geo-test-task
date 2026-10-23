import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from geotrack.problems import Conflict, NotFound
from geotrack.zones.schemas import GeozoneIn, GeozonePatch
from geotrack.zones.service import GeozoneService
from tests.fakes import MemoryZones, RecordingBus


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 23, 10, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def service(
    zones: MemoryZones | None = None, bus: RecordingBus | None = None, limit: int = 3
) -> GeozoneService:
    return GeozoneService(
        zones or MemoryZones(),
        bus or RecordingBus(),
        per_user_limit=limit,
        clock=Clock(),
    )


CENTER = GeozoneIn(name="office", latitude=50.45, longitude=30.52, radius_m=500)


async def test_create_stores_and_announces() -> None:
    bus = RecordingBus()
    zones = service(bus=bus)
    zone = await zones.create("alice", CENTER)
    assert (zone.owner_id, zone.name, zone.radius_m) == ("alice", "office", 500)
    assert zone.created_at == zone.updated_at
    owner, frame = bus.frames[0]
    assert owner == "alice"
    assert json.loads(frame) == {
        "kind": "geozones_changed",
        "action": "created",
        "id": str(zone.id),
    }


async def test_quota_is_per_owner() -> None:
    zones = service(limit=2)
    await zones.create("alice", CENTER)
    await zones.create("alice", CENTER)
    with pytest.raises(Conflict):
        await zones.create("alice", CENTER)
    await zones.create("bob", CENTER)


async def test_other_owners_zone_is_not_found() -> None:
    zones = service()
    zone = await zones.create("alice", CENTER)
    for action in (
        zones.get("bob", zone.id),
        zones.patch("bob", zone.id, GeozonePatch(radius_m=1)),
        zones.replace("bob", zone.id, CENTER),
        zones.delete("bob", zone.id),
    ):
        with pytest.raises(NotFound):
            await action
    assert (await zones.get("alice", zone.id)).id == zone.id


async def test_replace_overwrites_every_field() -> None:
    zones = service()
    zone = await zones.create("alice", CENTER)
    replaced = await zones.replace(
        "alice", zone.id, GeozoneIn(latitude=1, longitude=2, radius_m=3)
    )
    assert (
        replaced.name,
        replaced.latitude,
        replaced.longitude,
        replaced.radius_m,
    ) == (
        "",
        1,
        2,
        3,
    )
    assert replaced.updated_at > replaced.created_at


async def test_patch_changes_only_sent_fields() -> None:
    bus = RecordingBus()
    zones = service(bus=bus)
    zone = await zones.create("alice", CENTER)
    patched = await zones.patch("alice", zone.id, GeozonePatch(radius_m=900))
    assert (patched.name, patched.latitude, patched.radius_m) == ("office", 50.45, 900)
    assert json.loads(bus.frames[-1][1])["action"] == "updated"


async def test_empty_patch_changes_nothing_and_stays_quiet() -> None:
    bus = RecordingBus()
    zones = service(bus=bus)
    zone = await zones.create("alice", CENTER)
    before = zone.updated_at
    same = await zones.patch("alice", zone.id, GeozonePatch())
    assert same.updated_at == before
    assert len(bus.frames) == 1


async def test_delete_announces_and_removes() -> None:
    bus = RecordingBus()
    zones = service(bus=bus)
    zone = await zones.create("alice", CENTER)
    await zones.delete("alice", zone.id)
    assert json.loads(bus.frames[-1][1])["action"] == "deleted"
    with pytest.raises(NotFound):
        await zones.delete("alice", zone.id)


async def test_bus_outage_does_not_fail_the_change() -> None:
    zones = service(bus=RecordingBus(broken=True))
    zone = await zones.create("alice", CENTER)
    await zones.patch("alice", zone.id, GeozonePatch(name="renamed"))


async def test_list_is_paged_in_creation_order() -> None:
    zones = service(limit=10)
    created = [await zones.create("alice", CENTER) for _ in range(4)]
    page = await zones.list("alice", limit=2, offset=1)
    assert [z.id for z in page] == [created[1].id, created[2].id]
    assert await zones.list("bob", limit=10, offset=0) == []


async def test_missing_zone() -> None:
    with pytest.raises(NotFound):
        await service().get("alice", uuid4())
