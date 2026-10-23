from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from geotrack.db.tables import Geozone


class GeozoneRepository(Protocol):
    """Persistence of zones; every lookup is scoped to the owner."""

    async def insert(self, zone: Geozone, *, limit: int) -> bool:
        """Store ``zone`` unless its owner already has ``limit`` zones."""

    async def find(self, owner: str, zone_id: UUID) -> Geozone | None: ...

    async def page(self, owner: str, *, limit: int, offset: int) -> list[Geozone]: ...

    async def change(
        self, owner: str, zone_id: UUID, values: Mapping[str, object]
    ) -> Geozone | None: ...

    async def remove(self, owner: str, zone_id: UUID) -> bool: ...


class SqlGeozoneRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def insert(self, zone: Geozone, *, limit: int) -> bool:
        async with self._sessions.begin() as session:
            # Serialises creates per owner so the quota holds under concurrency.
            await session.execute(
                select(
                    func.pg_advisory_xact_lock(func.hashtextextended(zone.owner_id, 0))
                )
            )
            owned = await session.scalar(
                select(func.count()).where(Geozone.owner_id == zone.owner_id)
            )
            if (owned or 0) >= limit:
                return False
            session.add(zone)
        return True

    async def find(self, owner: str, zone_id: UUID) -> Geozone | None:
        async with self._sessions() as session:
            zone: Geozone | None = await session.scalar(
                select(Geozone).where(Geozone.id == zone_id, Geozone.owner_id == owner)
            )
            return zone

    async def page(self, owner: str, *, limit: int, offset: int) -> list[Geozone]:
        async with self._sessions() as session:
            result = await session.scalars(
                select(Geozone)
                .where(Geozone.owner_id == owner)
                .order_by(Geozone.created_at, Geozone.id)
                .limit(limit)
                .offset(offset)
            )
            return list(result)

    async def change(
        self, owner: str, zone_id: UUID, values: Mapping[str, object]
    ) -> Geozone | None:
        async with self._sessions.begin() as session:
            zone: Geozone | None = await session.scalar(
                select(Geozone)
                .where(Geozone.id == zone_id, Geozone.owner_id == owner)
                .with_for_update()
            )
            if zone is None:
                return None
            for attribute, value in values.items():
                setattr(zone, attribute, value)
        return zone

    async def remove(self, owner: str, zone_id: UUID) -> bool:
        async with self._sessions.begin() as session:
            removed = await session.scalar(
                delete(Geozone)
                .where(Geozone.id == zone_id, Geozone.owner_id == owner)
                .returning(Geozone.id)
            )
        return removed is not None
