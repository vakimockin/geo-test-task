import asyncio
from datetime import UTC, datetime

import pytest

from geotrack.telemetry.model import PositionReport
from geotrack.telemetry.pipeline import PositionPipeline

NOW = datetime(2026, 10, 23, 10, tzinfo=UTC)


def reports(count: int, prefix: str = "d") -> list[PositionReport]:
    return [PositionReport(f"{prefix}{i}", 50.0, 30.0, NOW) for i in range(count)]


class Sink:
    def __init__(self) -> None:
        self.batches: list[list[PositionReport]] = []
        self.release = asyncio.Event()
        self.release.set()
        self.fail_next = False

    async def __call__(self, batch: list[PositionReport]) -> None:
        await self.release.wait()
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("database went away")
        self.batches.append(batch)


def pipeline(sink: Sink, **overrides: float) -> PositionPipeline:
    options: dict[str, float] = {
        "capacity": 10,
        "batch_size": 4,
        "max_delay": 0.02,
        "drain_timeout": 1.0,
    }
    options.update(overrides)
    return PositionPipeline(
        sink,
        capacity=int(options["capacity"]),
        batch_size=int(options["batch_size"]),
        max_delay=options["max_delay"],
        drain_timeout=options["drain_timeout"],
    )


async def until(condition: object, timeout: float = 1.0) -> None:
    async def poll() -> None:
        while not condition():  # type: ignore[operator]
            await asyncio.sleep(0.001)

    await asyncio.wait_for(poll(), timeout)


def test_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError):
        PositionPipeline(Sink(), capacity=0, batch_size=1, max_delay=1, drain_timeout=1)


async def test_offer_takes_only_what_fits() -> None:
    queue = pipeline(Sink())
    assert queue.offer(reports(7)) == 7
    assert queue.offer(reports(7, "x")) == 3
    assert queue.offer(reports(1, "y")) == 0
    assert queue.depth == 10
    assert (queue.stats.accepted, queue.stats.rejected) == (10, 5)


async def test_full_batches_flush_without_waiting_for_the_delay() -> None:
    sink = Sink()
    queue = pipeline(sink, max_delay=10.0)
    queue.start()
    queue.offer(reports(8))
    await until(lambda: len(sink.batches) == 2)
    assert [len(b) for b in sink.batches] == [4, 4]
    await queue.close()


async def test_partial_batch_flushes_after_max_delay() -> None:
    sink = Sink()
    queue = pipeline(sink)
    queue.start()
    queue.offer(reports(3))
    await until(lambda: len(sink.batches) == 1)
    assert len(sink.batches[0]) == 3
    assert queue.depth == 0
    await queue.close()


async def test_in_flight_items_count_against_capacity() -> None:
    sink = Sink()
    sink.release.clear()
    queue = pipeline(sink, capacity=6)
    queue.start()
    queue.offer(reports(4))
    await asyncio.sleep(0.05)
    assert queue.depth == 4
    assert queue.offer(reports(5, "x")) == 2
    sink.release.set()
    await queue.close()
    assert sum(len(b) for b in sink.batches) == 6


async def test_failed_batch_is_counted_and_the_flusher_keeps_going() -> None:
    sink = Sink()
    sink.fail_next = True
    queue = pipeline(sink)
    queue.start()
    queue.offer(reports(4))
    await until(lambda: queue.stats.failed == 4)
    queue.offer(reports(2, "x"))
    await until(lambda: queue.stats.flushed == 2)
    assert queue.running
    await queue.close()


async def test_close_drains_everything_queued() -> None:
    sink = Sink()
    queue = pipeline(sink, max_delay=5.0)
    queue.start()
    queue.offer(reports(10))
    await queue.close()
    assert sum(len(b) for b in sink.batches) == 10
    assert not queue.running
    assert queue.offer(reports(1, "late")) == 0


async def test_close_gives_up_after_drain_timeout_and_counts_losses() -> None:
    sink = Sink()
    sink.release.clear()
    queue = pipeline(sink, drain_timeout=0.05)
    queue.start()
    queue.offer(reports(6))
    await asyncio.sleep(0.03)
    await queue.close()
    assert queue.stats.lost == 6
    assert queue.depth == 0
    assert sink.batches == []


async def test_close_without_start_is_harmless() -> None:
    queue = pipeline(Sink())
    await queue.close()
    assert queue.stats.lost == 0


class Outage:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.batches: list[list[PositionReport]] = []

    async def __call__(self, batch: list[PositionReport]) -> None:
        if self.failures:
            self.failures -= 1
            raise ConnectionRefusedError("database is restarting")
        self.batches.append(batch)


def retrying(sink: Outage, capacity: int = 10) -> PositionPipeline:
    return PositionPipeline(
        sink,
        capacity=capacity,
        batch_size=4,
        max_delay=0.01,
        drain_timeout=1.0,
        retryable=lambda exc: isinstance(exc, OSError),
        backoff=(0.01, 0.02),
    )


async def test_transient_failures_are_retried_with_the_same_batch() -> None:
    sink = Outage(failures=3)
    queue = retrying(sink)
    queue.start()
    queue.offer(reports(4))
    await until(lambda: queue.stats.flushed == 4)
    assert queue.stats.retries == 3 and queue.stats.failed == 0
    assert [r.device_id for r in sink.batches[0]] == ["d0", "d1", "d2", "d3"]
    await queue.close()


async def test_retrying_batch_keeps_its_capacity_so_ingest_backs_up() -> None:
    sink = Outage(failures=1_000)
    queue = retrying(sink, capacity=6)
    queue.start()
    queue.offer(reports(4))
    await until(lambda: queue.stats.retries >= 2)
    assert queue.offer(reports(5, "x")) == 2
    assert queue.offer(reports(1, "y")) == 0
    sink.failures = 0
    await until(lambda: queue.stats.flushed == 6)
    await queue.close()


async def test_close_stops_retrying() -> None:
    sink = Outage(failures=1_000)
    queue = retrying(sink)
    queue.start()
    queue.offer(reports(4))
    await until(lambda: queue.stats.retries >= 1)
    await queue.close()
    assert queue.stats.failed == 4 and queue.stats.lost == 0
