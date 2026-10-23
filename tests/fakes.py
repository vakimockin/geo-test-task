from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from geotrack.db.tables import Geozone


class MemoryZones:
    """In-memory GeozoneRepository with the same owner scoping as the SQL one."""

    def __init__(self) -> None:
        self.rows: dict[UUID, Geozone] = {}

    async def insert(self, zone: Geozone, *, limit: int) -> bool:
        if sum(z.owner_id == zone.owner_id for z in self.rows.values()) >= limit:
            return False
        self.rows[zone.id] = zone
        return True

    async def find(self, owner: str, zone_id: UUID) -> Geozone | None:
        zone = self.rows.get(zone_id)
        return zone if zone is not None and zone.owner_id == owner else None

    async def page(self, owner: str, *, limit: int, offset: int) -> list[Geozone]:
        owned = sorted(
            (z for z in self.rows.values() if z.owner_id == owner),
            key=lambda z: (z.created_at, z.id),
        )
        return owned[offset : offset + limit]

    async def change(
        self, owner: str, zone_id: UUID, values: Mapping[str, object]
    ) -> Geozone | None:
        zone = await self.find(owner, zone_id)
        if zone is not None:
            for name, value in values.items():
                setattr(zone, name, value)
        return zone

    async def remove(self, owner: str, zone_id: UUID) -> bool:
        if await self.find(owner, zone_id) is None:
            return False
        del self.rows[zone_id]
        return True


@dataclass
class RecordingBus:
    """Stands in for RedisBus on the publishing side."""

    frames: list[tuple[str, str]] = field(default_factory=list)
    broken: bool = False

    async def publish_frames(self, items: Sequence[tuple[str, str]]) -> None:
        if self.broken:
            raise ConnectionError("bus is down")
        self.frames.extend(items)
