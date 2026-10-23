import asyncio
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

from geotrack.app import RuntimeBuilder
from geotrack.config import Settings
from geotrack.db.tables import Geozone
from geotrack.runtime import Runtime
from geotrack.telemetry.intake import Intake, TimeWindow
from geotrack.telemetry.model import PositionReport
from geotrack.telemetry.pipeline import PositionPipeline
from geotrack.zones.service import GeozoneService


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


class FakeSocket:
    """Records frames; optionally blocks every send until released."""

    def __init__(self, *, gated: bool = False) -> None:
        self.sent: list[str] = []
        self.closed: tuple[int, str | None] | None = None
        self.gate = asyncio.Event()
        if not gated:
            self.gate.set()

    async def send_text(self, data: str) -> None:
        await self.gate.wait()
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = (code, reason)


@dataclass
class StubRuntimeParts:
    """What a test needs to reach behind the HTTP layer."""

    zones: MemoryZones
    bus: RecordingBus
    flushed: list[list[PositionReport]]
    runtime: Runtime


def stub_runtime(settings: Settings) -> StubRuntimeParts:
    zones = MemoryZones()
    bus = RecordingBus()
    flushed: list[list[PositionReport]] = []

    async def flush(batch: list[PositionReport]) -> None:
        flushed.append(batch)

    pipeline = PositionPipeline(
        flush,
        capacity=settings.pipeline_capacity,
        batch_size=settings.pipeline_batch_size,
        max_delay=settings.pipeline_max_delay,
        drain_timeout=settings.pipeline_drain_timeout,
    )
    window = TimeWindow(
        max_future=timedelta(seconds=settings.report_max_future_seconds),
        max_age=timedelta(seconds=settings.report_max_age_seconds),
    )
    runtime = Runtime(
        settings=settings,
        zones=GeozoneService(zones, bus, per_user_limit=settings.max_geozones_per_user),
        intake=Intake(pipeline, window),
        pipeline=pipeline,
    )
    return StubRuntimeParts(zones, bus, flushed, runtime)


def builder_for(parts: StubRuntimeParts) -> RuntimeBuilder:
    async def build(settings: Settings, stack: AsyncExitStack) -> Runtime:
        return parts.runtime

    return build
