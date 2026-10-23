import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from geotrack.telemetry.model import (
    AppliedPosition,
    PositionReport,
    StoreOutcome,
    ZoneHit,
)
from geotrack.telemetry.processor import (
    ALERT_ITEMS_PER_FRAME,
    TelemetryProcessor,
    alert_frames,
)
from tests.fakes import RecordingBus

NOW = datetime(2026, 10, 23, 10, tzinfo=UTC)


class Store:
    def __init__(self, outcome: StoreOutcome) -> None:
        self.outcome = outcome
        self.calls: list[Sequence[PositionReport]] = []

    async def apply(self, reports: Sequence[PositionReport]) -> StoreOutcome:
        self.calls.append(reports)
        return self.outcome


def hit(owner: str, zone: UUID, device: str = "d") -> ZoneHit:
    return ZoneHit(owner, zone, device, 50.0, 30.0, NOW)


async def test_publishes_applied_positions_and_alerts_per_owner() -> None:
    zone_a, zone_b = uuid4(), uuid4()
    applied = [AppliedPosition("d", 50.0, 30.0, NOW)]
    store = Store(StoreOutcome(applied, [hit("alice", zone_a), hit("bob", zone_b)]))
    bus = RecordingBus()
    processor = TelemetryProcessor(store, bus)

    await processor([PositionReport("d", 50.0, 30.0, NOW)])

    assert bus.positions == applied
    owners = [owner for owner, _ in bus.frames]
    assert owners == ["alice", "bob"]
    alice_frame = json.loads(bus.frames[0][1])
    assert alice_frame["kind"] == "alert"
    assert alice_frame["items"][0]["geozone_id"] == str(zone_a)


async def test_nothing_applied_means_nothing_published() -> None:
    bus = RecordingBus()
    await TelemetryProcessor(Store(StoreOutcome([], [])), bus)([])
    assert bus.positions == [] and bus.frames == []


async def test_bus_failure_does_not_fail_the_batch() -> None:
    applied = [AppliedPosition("d", 50.0, 30.0, NOW)]
    bus = RecordingBus(broken=True)
    processor = TelemetryProcessor(
        Store(StoreOutcome(applied, [hit("a", uuid4())])), bus
    )
    await processor([PositionReport("d", 50.0, 30.0, NOW)])
    assert processor.publish_errors == 2


def test_alert_frames_are_chunked_per_owner() -> None:
    zone = uuid4()
    hits = [hit("alice", zone, f"d{i}") for i in range(ALERT_ITEMS_PER_FRAME + 1)]
    hits.append(hit("bob", zone))
    framed = alert_frames(hits)
    assert [owner for owner, _ in framed] == ["alice", "alice", "bob"]
    sizes = [len(json.loads(frame)["items"]) for _, frame in framed]
    assert sizes == [ALERT_ITEMS_PER_FRAME, 1, 1]
