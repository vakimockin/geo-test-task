import asyncio
import json
from typing import Any

from geotrack.geo import Viewport
from geotrack.live.cache import PositionCache
from geotrack.live.client import CLOSE_TOO_SLOW, LiveClient
from tests.fakes import FakeSocket


def frames_of(socket: FakeSocket, kind: str) -> list[dict[str, Any]]:
    decoded = [json.loads(raw) for raw in socket.sent]
    return [frame for frame in decoded if frame["kind"] == kind]


def device_ids(socket: FakeSocket) -> list[str]:
    return [
        item["device_id"]
        for frame in frames_of(socket, "positions")
        for item in frame["items"]
    ]


def client(
    socket: FakeSocket,
    cache: PositionCache,
    *,
    interval: float = 0.05,
    items: int = 100,
    backlog: int = 10,
) -> LiveClient:
    return LiveClient(
        "alice",
        socket,
        cache,
        push_interval=interval,
        frame_items=items,
        backlog=backlog,
    )


def cache() -> PositionCache:
    return PositionCache(cell_degrees=0.25, ttl=60)


async def run_for(live: LiveClient, seconds: float) -> None:
    task = asyncio.create_task(live.run())
    await asyncio.sleep(seconds)
    live.stop()
    await asyncio.wait_for(task, 1)


async def test_first_push_is_a_snapshot_then_only_changes() -> None:
    positions = cache()
    positions.apply([("a", 1.0, 1.0, 1), ("b", 1.0, 1.0, 1)])
    socket = FakeSocket()
    live = client(socket, positions)
    task = asyncio.create_task(live.run())
    await asyncio.sleep(0.02)
    positions.apply([("b", 2.0, 2.0, 2)])
    await asyncio.sleep(0.08)
    live.stop()
    await task
    assert device_ids(socket) == ["a", "b", "b"]


async def test_control_frames_go_out_immediately() -> None:
    socket = FakeSocket()
    live = client(socket, cache(), interval=10)
    task = asyncio.create_task(live.run())
    await asyncio.sleep(0.01)
    live.push('{"kind":"alert","items":[]}')
    await asyncio.sleep(0.01)
    assert frames_of(socket, "alert")
    live.stop()
    await task


async def test_many_updates_between_pushes_are_coalesced() -> None:
    positions = cache()
    socket = FakeSocket()
    live = client(socket, positions, interval=0.1)
    task = asyncio.create_task(live.run())
    await asyncio.sleep(0.01)
    for step in range(20):
        positions.apply([("a", 1.0, 1.0 + step / 100, step + 1)])
    await asyncio.sleep(0.15)
    live.stop()
    await task
    assert device_ids(socket) == ["a"]
    last = frames_of(socket, "positions")[-1]["items"][0]
    assert last["longitude"] == 1.19


async def test_new_viewport_triggers_a_full_view_at_once() -> None:
    positions = cache()
    positions.apply([("kyiv", 50.45, 30.5, 1), ("far", 10.0, 10.0, 1)])
    socket = FakeSocket()
    live = client(socket, positions, interval=10)
    task = asyncio.create_task(live.run())
    await asyncio.sleep(0.01)
    socket.sent.clear()
    live.watch(Viewport(30.4, 50.4, 30.6, 50.5))
    await asyncio.sleep(0.01)
    assert device_ids(socket) == ["kyiv"]
    live.watch(None)
    await asyncio.sleep(0.01)
    assert device_ids(socket) == ["kyiv", "kyiv", "far"]
    live.stop()
    await task


async def test_large_updates_are_split_into_frames() -> None:
    positions = cache()
    positions.apply([(f"d{i}", 1.0, 1.0, 1) for i in range(25)])
    socket = FakeSocket()
    await run_for(client(socket, positions, items=10), 0.02)
    sizes = [len(f["items"]) for f in frames_of(socket, "positions")]
    assert sizes == [10, 10, 5]


async def test_alert_backlog_overflow_closes_with_1013() -> None:
    socket = FakeSocket(gated=True)
    live = client(socket, cache(), backlog=3)
    task = asyncio.create_task(live.run())
    accepted = [live.push(f'{{"kind":"alert","n":{n}}}') for n in range(5)]
    socket.gate.set()
    await asyncio.wait_for(task, 1)
    assert accepted[:3] == [True, True, True]
    assert accepted[3] is False and live.overflowed
    assert socket.closed is not None and socket.closed[0] == CLOSE_TOO_SLOW


async def test_stopped_client_refuses_frames() -> None:
    live = client(FakeSocket(), cache())
    live.stop()
    assert live.push("{}") is False
