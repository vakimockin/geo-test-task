import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import pytest_asyncio
from fakeredis import FakeAsyncRedis

from geotrack.live.bus import POSITIONS_CHANNEL, RedisBus, user_channel
from geotrack.live.frames import BusRow
from geotrack.telemetry.model import AppliedPosition

NOW = datetime(2026, 10, 23, 10, tzinfo=UTC)


class Sink:
    def __init__(self) -> None:
        self.rows: list[BusRow] = []
        self.frames: list[tuple[str, str]] = []

    def positions(self, rows: list[BusRow]) -> None:
        self.rows.extend(rows)

    def to_user(self, user: str, frame: str) -> int:
        self.frames.append((user, frame))
        return 1


class FlakyRedis:
    """Delegates to fakeredis, but the first ``failures`` pubsub() calls fail."""

    def __init__(self, inner: FakeAsyncRedis, failures: int) -> None:
        self._inner = inner
        self.failures = failures

    def pubsub(self, **kwargs: Any) -> Any:
        if self.failures:
            self.failures -= 1
            raise ConnectionError("redis restarting")
        return self._inner.pubsub(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@pytest_asyncio.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis(decode_responses=True)
    yield client
    await client.aclose()


async def eventually(check: Callable[[], bool], timeout: float = 2.0) -> None:
    async def poll() -> None:
        while not check():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


async def listeners_become(
    redis: FakeAsyncRedis, channel: str, count: int, timeout: float = 2.0
) -> None:
    async def poll() -> None:
        while await redis.publish(channel, "probe") != count:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


async def start(bus: RedisBus) -> asyncio.Task[None]:
    task = asyncio.create_task(bus.run())
    await eventually(lambda: bus.connected)
    return task


async def stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_positions_round_trip(redis: FakeAsyncRedis) -> None:
    sink = Sink()
    bus = RedisBus(redis, sink)
    task = await start(bus)
    await bus.publish_positions([AppliedPosition("d1", 50.0, 30.0, NOW)])
    await eventually(lambda: bool(sink.rows))
    assert sink.rows == [("d1", 50.0, 30.0, int(NOW.timestamp() * 1000))]
    await stop(task)
    assert not bus.connected


async def test_user_channel_is_subscribed_only_while_followed(
    redis: FakeAsyncRedis,
) -> None:
    sink = Sink()
    bus = RedisBus(redis, sink)
    task = await start(bus)
    channel = user_channel("alice")
    assert await redis.publish(channel, "early") == 0

    bus.follow("alice")
    await listeners_become(redis, channel, 1)
    await bus.publish_frames([("alice", "A1"), ("bob", "B1")])
    await eventually(lambda: ("alice", "A1") in sink.frames)
    assert all(user == "alice" for user, _ in sink.frames)

    bus.unfollow("alice")
    await listeners_become(redis, channel, 0)
    await stop(task)


async def test_malformed_position_payload_is_skipped(redis: FakeAsyncRedis) -> None:
    sink = Sink()
    bus = RedisBus(redis, sink)
    task = await start(bus)
    await redis.publish(POSITIONS_CHANNEL, "{not json")
    await bus.publish_positions([AppliedPosition("ok", 1.0, 1.0, NOW)])
    await eventually(lambda: bool(sink.rows))
    assert bus.malformed == 1
    await stop(task)


async def test_reconnects_and_restores_user_subscriptions(
    redis: FakeAsyncRedis,
) -> None:
    sink = Sink()
    flaky = FlakyRedis(redis, failures=2)
    bus = RedisBus(flaky, sink, backoff_start=0.01)  # type: ignore[arg-type]
    bus.follow("alice")
    task = asyncio.create_task(bus.run())
    await eventually(lambda: bus.connected)
    assert flaky.failures == 0
    await listeners_become(redis, user_channel("alice"), 1)
    await stop(task)


async def test_publishing_nothing_is_a_no_op(redis: FakeAsyncRedis) -> None:
    bus = RedisBus(redis, Sink())
    await bus.publish_positions([])
    await bus.publish_frames([])
