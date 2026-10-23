import asyncio
import contextlib
import logging
import random
from collections.abc import Sequence
from typing import Any, Protocol

from redis.asyncio import Redis

from geotrack.live import frames
from geotrack.live.frames import BusRow
from geotrack.telemetry.model import AppliedPosition

log = logging.getLogger(__name__)

POSITIONS_CHANNEL = "geotrack:positions"
USER_CHANNEL_PREFIX = "geotrack:user:"


def user_channel(user: str) -> str:
    return USER_CHANNEL_PREFIX + user


class BusSink(Protocol):
    def positions(self, rows: list[BusRow]) -> None: ...

    def to_user(self, user: str, frame: str) -> int: ...


class RedisBus:
    """Redis Pub/Sub between instances; user channels are joined only while needed."""

    def __init__(
        self,
        redis: Redis,
        sink: BusSink,
        *,
        poll_timeout: float = 0.05,
        backoff_start: float = 0.25,
        backoff_cap: float = 8.0,
    ) -> None:
        self._redis = redis
        self._sink = sink
        self._poll_timeout = poll_timeout
        self._backoff_start = backoff_start
        self._backoff_cap = backoff_cap
        self._wanted: set[str] = set()
        self._dirty = False
        self._failures = 0
        self.connected = False
        self.malformed = 0

    async def publish_positions(self, applied: Sequence[AppliedPosition]) -> None:
        if applied:
            await self._redis.publish(POSITIONS_CHANNEL, frames.pack_rows(applied))

    async def publish_frames(self, items: Sequence[tuple[str, str]]) -> None:
        if not items:
            return
        async with self._redis.pipeline(transaction=False) as pipe:
            for user, frame in items:
                pipe.publish(user_channel(user), frame)
            await pipe.execute()

    def follow(self, user: str) -> None:
        self._wanted.add(user)
        self._dirty = True

    def unfollow(self, user: str) -> None:
        self._wanted.discard(user)
        self._dirty = True

    async def run(self) -> None:
        while True:
            try:
                await self._listen()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._failures += 1
                delay = min(
                    self._backoff_cap, self._backoff_start * 2 ** (self._failures - 1)
                ) * random.uniform(0.5, 1.0)
                log.warning("bus connection lost (%s), retrying in %.2fs", exc, delay)
                await asyncio.sleep(delay)

    async def _listen(self) -> None:
        pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        try:
            await pubsub.subscribe(POSITIONS_CHANNEL)
            joined: set[str] = set()
            self._dirty = True
            self.connected = True
            self._failures = 0
            while True:
                if self._dirty:
                    await self._resubscribe(pubsub, joined)
                message = await pubsub.get_message(timeout=self._poll_timeout)
                if message is not None:
                    self.dispatch(message)
        finally:
            self.connected = False
            with contextlib.suppress(Exception):
                await pubsub.aclose()  # type: ignore[no-untyped-call]

    async def _resubscribe(self, pubsub: Any, joined: set[str]) -> None:
        self._dirty = False
        wanted = set(self._wanted)
        if added := wanted - joined:
            await pubsub.subscribe(*(user_channel(u) for u in added))
        if dropped := joined - wanted:
            await pubsub.unsubscribe(*(user_channel(u) for u in dropped))
        joined.clear()
        joined.update(wanted)

    def dispatch(self, message: dict[str, Any]) -> None:
        if message.get("type") != "message":
            return
        channel = _text(message["channel"])
        try:
            if channel == POSITIONS_CHANNEL:
                self._sink.positions(frames.unpack_rows(message["data"]))
            elif channel.startswith(USER_CHANNEL_PREFIX):
                user = channel.removeprefix(USER_CHANNEL_PREFIX)
                self._sink.to_user(user, _text(message["data"]))
        except (ValueError, TypeError):
            self.malformed += 1
            log.warning("ignoring malformed message on %s", channel)


def _text(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value
