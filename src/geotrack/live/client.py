import asyncio
import logging
from collections import deque
from typing import Protocol

from geotrack.geo import Viewport
from geotrack.live import frames
from geotrack.live.cache import PositionCache

log = logging.getLogger(__name__)

# RFC 6455 "Try Again Later": the client could not keep up with its alerts.
CLOSE_TOO_SLOW = 1013


class Socket(Protocol):
    async def send_text(self, data: str) -> None: ...

    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


class LiveClient:
    """One browser session: alerts go out at once, positions on a fixed cadence."""

    def __init__(
        self,
        user: str,
        socket: Socket,
        cache: PositionCache,
        *,
        push_interval: float,
        frame_items: int,
        backlog: int,
    ) -> None:
        self.user = user
        self._socket = socket
        self._cache = cache
        self._push_interval = push_interval
        self._frame_items = frame_items
        self._backlog = backlog
        self._control: deque[str] = deque()
        self._viewport: Viewport | None = None
        self._cursor = 0
        self._refresh = True
        self._wake = asyncio.Event()
        self._overflowed = False
        self._stopped = False

    @property
    def viewport(self) -> Viewport | None:
        return self._viewport

    @property
    def overflowed(self) -> bool:
        return self._overflowed

    @property
    def pending(self) -> tuple[str, ...]:
        """Alert and control frames not sent yet."""
        return tuple(self._control)

    def push(self, frame: str) -> bool:
        if self._stopped:
            return False
        if len(self._control) >= self._backlog:
            log.warning("closing slow live session of user %s", self.user)
            self._overflowed = True
            self._stopped = True
            self._wake.set()
            return False
        self._control.append(frame)
        self._wake.set()
        return True

    def watch(self, viewport: Viewport | None) -> None:
        """Change the visible area; the next push sends all of it."""
        self._viewport = viewport
        self._cursor = 0
        self._refresh = True
        self._wake.set()

    def stop(self) -> None:
        self._stopped = True
        self._wake.set()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        due = loop.time()
        while not self._stopped:
            idle_for = due - loop.time()
            if not self._control and not self._refresh and idle_for > 0:
                try:
                    await asyncio.wait_for(self._wake.wait(), idle_for)
                except TimeoutError:
                    pass
                self._wake.clear()
                continue
            while self._control and not self._stopped:
                await self._socket.send_text(self._control.popleft())
            if self._stopped:
                break
            if self._refresh or loop.time() >= due:
                self._refresh = False
                await self._send_positions()
                due = loop.time() + self._push_interval
        if self._overflowed:
            await self._socket.close(CLOSE_TOO_SLOW, "too slow to receive alerts")

    async def _send_positions(self) -> None:
        cursor = self._cache.head
        items = self._cache.changed_since(self._cursor, self._viewport)
        self._cursor = cursor
        for start in range(0, len(items), self._frame_items):
            await self._socket.send_text(
                frames.positions(items[start : start + self._frame_items])
            )
