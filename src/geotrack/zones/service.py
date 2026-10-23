import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from geotrack.db.tables import Geozone
from geotrack.live import frames
from geotrack.live.frames import ZoneAction
from geotrack.problems import Conflict, NotFound
from geotrack.zones.repository import GeozoneRepository
from geotrack.zones.schemas import GeozoneIn, GeozonePatch

log = logging.getLogger(__name__)


class FramePublisher(Protocol):
    async def publish_frames(self, items: Sequence[tuple[str, str]]) -> None: ...


def _now() -> datetime:
    return datetime.now(UTC)


class GeozoneService:
    """Zone use cases for one owner at a time; other owners' zones look absent."""

    def __init__(
        self,
        repository: GeozoneRepository,
        publisher: FramePublisher,
        *,
        per_user_limit: int,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._repository = repository
        self._publisher = publisher
        self._per_user_limit = per_user_limit
        self._clock = clock

    async def create(self, owner: str, data: GeozoneIn) -> Geozone:
        moment = self._clock()
        zone = Geozone(
            id=uuid.uuid4(),
            owner_id=owner,
            name=data.name,
            latitude=data.latitude,
            longitude=data.longitude,
            radius_m=data.radius_m,
            created_at=moment,
            updated_at=moment,
        )
        if not await self._repository.insert(zone, limit=self._per_user_limit):
            raise Conflict(f"geozone limit reached ({self._per_user_limit} per user)")
        await self._announce(owner, "created", zone.id)
        return zone

    async def get(self, owner: str, zone_id: UUID) -> Geozone:
        zone = await self._repository.find(owner, zone_id)
        if zone is None:
            raise NotFound(f"geozone {zone_id} does not exist")
        return zone

    async def list(self, owner: str, *, limit: int, offset: int) -> list[Geozone]:
        return await self._repository.page(owner, limit=limit, offset=offset)

    async def replace(self, owner: str, zone_id: UUID, data: GeozoneIn) -> Geozone:
        return await self._update(owner, zone_id, data.model_dump())

    async def patch(self, owner: str, zone_id: UUID, patch: GeozonePatch) -> Geozone:
        values = patch.changes()
        if not values:
            return await self.get(owner, zone_id)
        return await self._update(owner, zone_id, values)

    async def delete(self, owner: str, zone_id: UUID) -> None:
        if not await self._repository.remove(owner, zone_id):
            raise NotFound(f"geozone {zone_id} does not exist")
        await self._announce(owner, "deleted", zone_id)

    async def _update(
        self, owner: str, zone_id: UUID, values: dict[str, object]
    ) -> Geozone:
        values["updated_at"] = self._clock()
        zone = await self._repository.change(owner, zone_id, values)
        if zone is None:
            raise NotFound(f"geozone {zone_id} does not exist")
        await self._announce(owner, "updated", zone_id)
        return zone

    async def _announce(self, owner: str, action: ZoneAction, zone_id: UUID) -> None:
        # the change is already committed, a missed notice must not fail it
        try:
            await self._publisher.publish_frames(
                [(owner, frames.geozones_changed(action, str(zone_id)))]
            )
        except Exception:
            log.warning("could not announce geozone change", exc_info=True)
