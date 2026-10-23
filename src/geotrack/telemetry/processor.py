import logging
from collections import defaultdict
from collections.abc import Awaitable, Sequence
from typing import Protocol

from geotrack.live import frames
from geotrack.telemetry.model import (
    AppliedPosition,
    PositionReport,
    StoreOutcome,
    ZoneHit,
)

log = logging.getLogger(__name__)

ALERT_ITEMS_PER_FRAME = 500


class OutcomeStore(Protocol):
    async def apply(self, reports: Sequence[PositionReport]) -> StoreOutcome: ...


class Broadcaster(Protocol):
    async def publish_positions(self, applied: Sequence[AppliedPosition]) -> None: ...

    async def publish_frames(self, frames: Sequence[tuple[str, str]]) -> None: ...


class TelemetryProcessor:
    """Stores a batch, then publishes positions and alerts (best effort)."""

    def __init__(self, store: OutcomeStore, broadcaster: Broadcaster) -> None:
        self._store = store
        self._broadcaster = broadcaster
        self.publish_errors = 0

    async def __call__(self, batch: list[PositionReport]) -> None:
        outcome = await self._store.apply(batch)
        if outcome.applied:
            await self._guarded(self._broadcaster.publish_positions(outcome.applied))
        if outcome.hits:
            await self._guarded(
                self._broadcaster.publish_frames(alert_frames(outcome.hits))
            )

    async def _guarded(self, publishing: Awaitable[None]) -> None:
        try:
            await publishing
        except Exception:
            self.publish_errors += 1
            log.warning("publishing to the bus failed", exc_info=True)


def alert_frames(hits: Sequence[ZoneHit]) -> list[tuple[str, str]]:
    """One or more ``(owner, frame)`` pairs per owner, in report order."""
    by_owner: dict[str, list[ZoneHit]] = defaultdict(list)
    for hit in hits:
        by_owner[hit.owner_id].append(hit)
    return [
        (owner, frames.alert(owned[start : start + ALERT_ITEMS_PER_FRAME]))
        for owner, owned in by_owner.items()
        for start in range(0, len(owned), ALERT_ITEMS_PER_FRAME)
    ]
