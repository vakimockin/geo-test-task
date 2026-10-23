import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from geotrack.geo import Cell, Viewport, cell_of
from geotrack.live import frames
from geotrack.live.frames import BusRow


@dataclass(slots=True)
class _Entry:
    device_id: str
    lat: float
    lon: float
    at_ms: int
    seq: int
    cell: Cell
    seen: float
    item: str


class PositionCache:
    """Latest position per device, kept in change order and bucketed by grid cell."""

    def __init__(
        self,
        *,
        cell_degrees: float,
        ttl: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cell_degrees = cell_degrees
        self._ttl = ttl
        self._clock = clock
        self._log: OrderedDict[str, _Entry] = OrderedDict()
        self._cells: dict[Cell, OrderedDict[str, _Entry]] = {}
        self._seq = 0

    def __len__(self) -> int:
        return len(self._log)

    @property
    def head(self) -> int:
        return self._seq

    def apply(self, rows: Iterable[BusRow]) -> int:
        changed = 0
        now = self._clock()
        for device_id, lat, lon, at_ms in rows:
            previous = self._log.get(device_id)
            if previous is not None:
                if previous.at_ms >= at_ms:
                    continue
                self._forget(previous)
            self._seq += 1
            entry = _Entry(
                device_id=device_id,
                lat=lat,
                lon=lon,
                at_ms=at_ms,
                seq=self._seq,
                cell=cell_of(lat, lon, self._cell_degrees),
                seen=now,
                item=frames.position_item(device_id, lat, lon, at_ms),
            )
            self._log[device_id] = entry
            self._cells.setdefault(entry.cell, OrderedDict())[device_id] = entry
            changed += 1
        return changed

    def changed_since(self, cursor: int, viewport: Viewport | None) -> list[str]:
        """Encoded items for devices updated after ``cursor`` inside the viewport."""
        if viewport is None:
            return [e.item for e in _newer(self._log, cursor)]
        cells = viewport.cells(self._cell_degrees)
        if cells is None:
            return [
                e.item
                for e in _newer(self._log, cursor)
                if viewport.covers(e.lat, e.lon)
            ]
        found: list[_Entry] = []
        for cell in cells:
            bucket = self._cells.get(cell)
            if bucket:
                found.extend(
                    e for e in _newer(bucket, cursor) if viewport.covers(e.lat, e.lon)
                )
        found.sort(key=lambda e: e.seq)
        return [e.item for e in found]

    def evict_stale(self) -> list[str]:
        """Drop devices silent for longer than the TTL; returns their ids."""
        cutoff = self._clock() - self._ttl
        evicted: list[str] = []
        while self._log:
            oldest = next(iter(self._log.values()))
            if oldest.seen > cutoff:
                break
            self._forget(oldest)
            evicted.append(oldest.device_id)
        return evicted

    def _forget(self, entry: _Entry) -> None:
        del self._log[entry.device_id]
        bucket = self._cells[entry.cell]
        del bucket[entry.device_id]
        if not bucket:
            del self._cells[entry.cell]


def _newer(log: "OrderedDict[str, _Entry]", cursor: int) -> Iterator[_Entry]:
    """Entries with ``seq > cursor``; logs are append-only in ``seq`` order."""
    newest_first = []
    for entry in reversed(log.values()):
        if entry.seq <= cursor:
            break
        newest_first.append(entry)
    return reversed(newest_first)
