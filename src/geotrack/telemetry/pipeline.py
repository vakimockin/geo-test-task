import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from geotrack.telemetry.model import PositionReport

log = logging.getLogger(__name__)

Flush = Callable[[list[PositionReport]], Awaitable[None]]
Retryable = Callable[[Exception], bool]


def never(exc: Exception) -> bool:
    return False


@dataclass(slots=True)
class PipelineStats:
    accepted: int = 0
    rejected: int = 0
    flushed: int = 0
    failed: int = 0
    retries: int = 0
    lost: int = 0


class PositionPipeline:
    """Bounded hand-off from HTTP handlers to the single database writer."""

    def __init__(
        self,
        flush: Flush,
        *,
        capacity: int,
        batch_size: int,
        max_delay: float,
        drain_timeout: float,
        retryable: Retryable = never,
        backoff: tuple[float, float] = (0.2, 5.0),
    ) -> None:
        if capacity < 1 or batch_size < 1 or max_delay <= 0:
            raise ValueError("capacity, batch_size and max_delay must be positive")
        self._flush = flush
        self._capacity = capacity
        self._batch_size = batch_size
        self._max_delay = max_delay
        self._drain_timeout = drain_timeout
        self._retryable = retryable
        self._backoff = backoff
        self._pending: list[PositionReport] = []
        self._in_flight = 0
        self._wakeup = asyncio.Event()
        self._closing = False
        self._stopped = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.stats = PipelineStats()

    @property
    def depth(self) -> int:
        return len(self._pending) + self._in_flight

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def offer(self, reports: Sequence[PositionReport]) -> int:
        room = 0 if self._closing else max(0, self._capacity - self.depth)
        taken = min(room, len(reports))
        if taken:
            was_empty = not self._pending
            self._pending.extend(reports[:taken])
            if was_empty or len(self._pending) >= self._batch_size:
                self._wakeup.set()
        self.stats.accepted += taken
        self.stats.rejected += len(reports) - taken
        return taken

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="position-pipeline")

    async def close(self) -> None:
        """Stop taking work and flush what is queued, within the drain timeout."""
        self._closing = True
        self._stopped.set()
        self._wakeup.set()
        task, self._task = self._task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), self._drain_timeout)
        except TimeoutError:
            lost = self.depth
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self._pending.clear()
            self._in_flight = 0
            self.stats.lost += lost
            log.error("pipeline drain timed out, %d reports lost", lost)

    async def _run(self) -> None:
        while True:
            if not self._pending:
                if self._closing:
                    return
                self._wakeup.clear()
                await self._wakeup.wait()
                continue
            await self._fill()
            batch = self._pending[: self._batch_size]
            del self._pending[: self._batch_size]
            await self._deliver(batch)

    async def _fill(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._max_delay
        while len(self._pending) < self._batch_size and not self._closing:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            self._wakeup.clear()
            try:
                await asyncio.wait_for(self._wakeup.wait(), remaining)
            except TimeoutError:
                return

    async def _deliver(self, batch: list[PositionReport]) -> None:
        # the batch stays counted in depth while it is retried, so ingest backs up
        self._in_flight = len(batch)
        delay, longest = self._backoff
        try:
            while True:
                try:
                    await self._flush(batch)
                except Exception as exc:
                    if self._closing or not self._retryable(exc):
                        self.stats.failed += len(batch)
                        log.exception("dropping batch of %d reports", len(batch))
                        return
                    self.stats.retries += 1
                    log.warning("flush failed (%s), retry in %.1fs", exc, delay)
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._stopped.wait(), delay)
                    delay = min(delay * 2, longest)
                else:
                    self.stats.flushed += len(batch)
                    return
        finally:
            self._in_flight = 0
