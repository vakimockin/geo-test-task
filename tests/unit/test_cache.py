import json

from geotrack.geo import Viewport
from geotrack.live.cache import PositionCache


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def ids(items: list[str]) -> list[str]:
    return [json.loads(item)["device_id"] for item in items]


def make(clock: Clock | None = None, ttl: float = 60.0) -> PositionCache:
    return PositionCache(cell_degrees=0.25, ttl=ttl, clock=clock or Clock())


KYIV = Viewport(30.4, 50.4, 30.6, 50.5)


def test_changes_since_cursor_are_returned_in_update_order() -> None:
    cache = make()
    cache.apply([("a", 50.45, 30.5, 1), ("b", 50.46, 30.5, 1)])
    cursor = cache.head
    cache.apply([("c", 50.47, 30.5, 1), ("a", 50.48, 30.5, 2)])
    assert ids(cache.changed_since(0, None)) == ["b", "c", "a"]
    assert ids(cache.changed_since(cursor, None)) == ["c", "a"]
    assert cache.changed_since(cache.head, None) == []


def test_older_reports_are_ignored() -> None:
    cache = make()
    cache.apply([("a", 50.45, 30.5, 10)])
    head = cache.head
    assert cache.apply([("a", 1.0, 1.0, 9), ("a", 1.0, 1.0, 10)]) == 0
    assert cache.head == head
    item = json.loads(cache.changed_since(0, None)[0])
    assert (item["latitude"], item["longitude"]) == (50.45, 30.5)


def test_viewport_filters_by_cell_and_exact_bounds() -> None:
    cache = make()
    cache.apply(
        [
            ("inside", 50.45, 30.5, 1),
            ("same-cell-outside", 50.45, 30.65, 1),
            ("far", 10.0, 10.0, 1),
        ]
    )
    assert ids(cache.changed_since(0, KYIV)) == ["inside"]


def test_device_moving_between_cells_is_found_in_the_new_cell_only() -> None:
    cache = make()
    cache.apply([("a", 10.0, 10.0, 1)])
    cache.apply([("a", 50.45, 30.5, 2)])
    assert ids(cache.changed_since(0, KYIV)) == ["a"]
    assert cache.changed_since(0, Viewport(9.9, 9.9, 10.1, 10.1)) == []


def test_huge_viewport_falls_back_to_scanning() -> None:
    cache = make()
    cache.apply([("north", 60.0, 10.0, 1), ("south", -60.0, 10.0, 1)])
    northern = Viewport(-180, 0, 180, 90)
    assert northern.cells(0.25) is None
    assert ids(cache.changed_since(0, northern)) == ["north"]


def test_wrapping_viewport() -> None:
    cache = make()
    cache.apply(
        [("east", 0.0, 179.95, 1), ("west", 0.0, -179.95, 1), ("mid", 0.0, 0.0, 1)]
    )
    box = Viewport(179.9, -0.1, -179.9, 0.1)
    assert sorted(ids(cache.changed_since(0, box))) == ["east", "west"]


def test_stale_devices_are_evicted_oldest_first() -> None:
    clock = Clock()
    cache = make(clock, ttl=30)
    cache.apply([("a", 1.0, 1.0, 1)])
    clock.now += 20
    cache.apply([("b", 1.0, 1.0, 1)])
    clock.now += 15
    assert cache.evict_stale() == ["a"]
    assert len(cache) == 1
    clock.now += 30
    assert cache.evict_stale() == ["b"]
    assert cache.changed_since(0, None) == []
    assert cache.changed_since(0, Viewport(0, 0, 2, 2)) == []


def test_update_refreshes_ttl() -> None:
    clock = Clock()
    cache = make(clock, ttl=30)
    cache.apply([("a", 1.0, 1.0, 1)])
    clock.now += 25
    cache.apply([("a", 1.0, 1.1, 2)])
    clock.now += 25
    assert cache.evict_stale() == []
