import asyncio
import logging
from typing import Protocol

from geotrack.live import frames
from geotrack.live.cache import PositionCache
from geotrack.live.client import LiveClient
from geotrack.live.frames import BusRow

log = logging.getLogger(__name__)

GONE_IDS_PER_FRAME = 1_000


class Subscriptions(Protocol):
    def follow(self, user: str) -> None: ...

    def unfollow(self, user: str) -> None: ...


class _NoSubscriptions:
    def follow(self, user: str) -> None:
        return None

    def unfollow(self, user: str) -> None:
        return None


class Hub:
    """Live sessions of this process grouped by user; a user may have many."""

    def __init__(self, cache: PositionCache) -> None:
        self.cache = cache
        self.subscriptions: Subscriptions = _NoSubscriptions()
        self._sessions: dict[str, set[LiveClient]] = {}

    def attach(self, client: LiveClient) -> None:
        sessions = self._sessions.setdefault(client.user, set())
        if not sessions:
            self.subscriptions.follow(client.user)
        sessions.add(client)

    def detach(self, client: LiveClient) -> None:
        sessions = self._sessions.get(client.user)
        if sessions is None or client not in sessions:
            return
        sessions.remove(client)
        if not sessions:
            del self._sessions[client.user]
            self.subscriptions.unfollow(client.user)

    def session_count(self, user: str | None = None) -> int:
        if user is not None:
            return len(self._sessions.get(user, ()))
        return sum(len(group) for group in self._sessions.values())

    def users(self) -> list[str]:
        return list(self._sessions)

    def to_user(self, user: str, frame: str) -> int:
        targets = list(self._sessions.get(user, ()))
        for client in targets:
            client.push(frame)
        return len(targets)

    def to_everyone(self, frame: str) -> int:
        targets = [c for group in self._sessions.values() for c in group]
        for client in targets:
            client.push(frame)
        return len(targets)

    def positions(self, rows: list[BusRow]) -> None:
        self.cache.apply(rows)

    def sweep(self) -> int:
        """Evict silent devices and tell every session to drop their markers."""
        evicted = self.cache.evict_stale()
        for start in range(0, len(evicted), GONE_IDS_PER_FRAME):
            self.to_everyone(frames.gone(evicted[start : start + GONE_IDS_PER_FRAME]))
        return len(evicted)

    async def sweep_forever(self, every: float) -> None:
        while True:
            await asyncio.sleep(every)
            try:
                self.sweep()
            except Exception:
                log.exception("live cache sweep failed")
