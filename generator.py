from __future__ import annotations

import argparse
import asyncio
import contextlib
import math
import random
import time
from collections import deque
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

METERS_PER_DEGREE = 111_320.0
STEERING_NOISE_RAD = 0.2
REPORT_EVERY_S = 5.0
BATCH_PATH = "/api/v1/positions"
MAX_CHUNK = 5_000


@dataclass(frozen=True, slots=True)
class Area:
    lat: float
    lon: float
    radius_m: float

    def random_point(self, rng: random.Random) -> tuple[float, float]:
        distance = self.radius_m * math.sqrt(rng.random())
        bearing = rng.uniform(0.0, 2 * math.pi)
        return shift(
            self.lat,
            self.lon,
            distance * math.cos(bearing),
            distance * math.sin(bearing),
        )


@dataclass(slots=True)
class Vehicle:
    device_id: str
    lat: float
    lon: float
    goal_lat: float
    goal_lon: float
    speed_mps: float


def shift(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Move a point by a small local offset (equirectangular approximation)."""
    new_lat = min(90.0, max(-90.0, lat + north_m / METERS_PER_DEGREE))
    scale = max(math.cos(math.radians(lat)), 1e-6)
    new_lon = lon + east_m / (METERS_PER_DEGREE * scale)
    new_lon = (new_lon + 180.0) % 360.0 - 180.0
    return new_lat, new_lon


def offset_to(
    lat: float, lon: float, goal_lat: float, goal_lon: float
) -> tuple[float, float]:
    """``(north_m, east_m)`` from a point to a nearby goal."""
    north = (goal_lat - lat) * METERS_PER_DEGREE
    east = (goal_lon - lon) * METERS_PER_DEGREE * math.cos(math.radians(lat))
    return north, east


def spawn_fleet(
    count: int,
    area: Area,
    rng: random.Random,
    speed_range: tuple[float, float] = (4.0, 16.0),
) -> list[Vehicle]:
    fleet = []
    for number in range(count):
        lat, lon = area.random_point(rng)
        goal_lat, goal_lon = area.random_point(rng)
        fleet.append(
            Vehicle(
                device_id=f"unit-{number:05d}",
                lat=lat,
                lon=lon,
                goal_lat=goal_lat,
                goal_lon=goal_lon,
                speed_mps=rng.uniform(*speed_range),
            )
        )
    return fleet


def drive(vehicle: Vehicle, seconds: float, area: Area, rng: random.Random) -> None:
    north, east = offset_to(
        vehicle.lat, vehicle.lon, vehicle.goal_lat, vehicle.goal_lon
    )
    remaining = math.hypot(north, east)
    step = vehicle.speed_mps * seconds
    if remaining <= step:
        vehicle.lat, vehicle.lon = vehicle.goal_lat, vehicle.goal_lon
        vehicle.goal_lat, vehicle.goal_lon = area.random_point(rng)
        return
    heading = math.atan2(east, north) + rng.gauss(0.0, STEERING_NOISE_RAD)
    vehicle.lat, vehicle.lon = shift(
        vehicle.lat, vehicle.lon, step * math.cos(heading), step * math.sin(heading)
    )


def chunks(items: Sequence[Vehicle], size: int) -> Iterator[Sequence[Vehicle]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def batch_body(vehicles: Sequence[Vehicle], stamp: str) -> dict[str, Any]:
    return {
        "positions": [
            {
                "device_id": v.device_id,
                "latitude": round(v.lat, 7),
                "longitude": round(v.lon, 7),
                "timestamp": stamp,
            }
            for v in vehicles
        ]
    }


def retry_after(response: httpx.Response, fallback: float = 1.0) -> float:
    try:
        return min(30.0, max(0.0, float(response.headers["Retry-After"])))
    except (KeyError, ValueError):
        return fallback


@dataclass(slots=True)
class Meter:
    reported: int = 0
    queued: int = 0
    rejected: int = 0
    throttled: int = 0
    failed: int = 0
    last_failure: str = ""
    latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=4_000))

    def fail(self, reason: str) -> None:
        self.failed += 1
        self.last_failure = reason

    def quantile(self, q: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))]

    def line(self, elapsed: float, rate: float) -> str:
        text = (
            f"t={elapsed:6.0f}s reported={self.reported} queued={self.queued} "
            f"rejected={self.rejected} throttled={self.throttled} "
            f"failed={self.failed} rate={rate:,.0f}/s "
            f"p50={self.quantile(0.5):.0f}ms p95={self.quantile(0.95):.0f}ms"
        )
        return f"{text} last_failure={self.last_failure!r}" if self.failed else text


async def post_chunk(
    client: httpx.AsyncClient, vehicles: Sequence[Vehicle], stamp: str, meter: Meter
) -> None:
    body = batch_body(vehicles, stamp)
    for _ in range(3):
        started = time.perf_counter()
        try:
            response = await client.post(BATCH_PATH, json=body)
        except httpx.HTTPError as exc:
            meter.fail(f"{type(exc).__name__}: {exc}")
            return
        meter.latencies_ms.append((time.perf_counter() - started) * 1000)
        if response.status_code == 202:
            result = response.json()
            meter.reported += len(vehicles)
            meter.queued += int(result["queued"])
            meter.rejected += int(result["rejected"])
            return
        if response.status_code != 503:
            meter.fail(f"HTTP {response.status_code}: {response.text[:160]}")
            return
        meter.throttled += 1
        await asyncio.sleep(retry_after(response))
    meter.fail("still overloaded after retries")


async def gateway(
    number: int,
    shard: Sequence[Vehicle],
    options: argparse.Namespace,
    client: httpx.AsyncClient,
    meter: Meter,
) -> None:
    loop = asyncio.get_running_loop()
    rng = random.Random(None if options.seed is None else options.seed + number)
    area = Area(options.lat, options.lon, options.radius_km * 1000)
    tick = loop.time() + number * options.period / options.workers
    while True:
        await asyncio.sleep(max(0.0, tick - loop.time()))
        for vehicle in shard:
            drive(vehicle, options.period, area, rng)
        stamp = datetime.now(UTC).isoformat()
        for part in chunks(shard, options.chunk):
            await post_chunk(client, part, stamp, meter)
        tick += options.period


async def progress(meter: Meter, started: float) -> None:
    previous, previous_at = 0, started
    while True:
        await asyncio.sleep(REPORT_EVERY_S)
        now = time.monotonic()
        rate = (meter.reported - previous) / (now - previous_at)
        print(meter.line(now - started, rate), flush=True)
        previous, previous_at = meter.reported, now


async def simulate(options: argparse.Namespace, meter: Meter) -> None:
    area = Area(options.lat, options.lon, options.radius_km * 1000)
    fleet = spawn_fleet(options.devices, area, random.Random(options.seed))
    shards = [fleet[i :: options.workers] for i in range(options.workers)]
    limits = httpx.Limits(
        max_connections=options.workers, max_keepalive_connections=options.workers
    )
    print(
        f"{options.devices} devices in {options.workers} gateways, every "
        f"{options.period:g}s, chunks of {options.chunk}, target {options.base_url}",
        flush=True,
    )
    async with httpx.AsyncClient(
        base_url=options.base_url,
        limits=limits,
        timeout=httpx.Timeout(15.0, connect=5.0),
    ) as client:
        tasks = [
            asyncio.create_task(gateway(i, shard, options, client, meter))
            for i, shard in enumerate(shards)
            if shard
        ]
        tasks.append(asyncio.create_task(progress(meter, time.monotonic())))
        try:
            if options.duration:
                await asyncio.sleep(options.duration)
            else:
                await asyncio.Event().wait()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report simulated vehicle positions to geotrack."
    )
    parser.add_argument("--devices", type=int, default=10_000, help="fleet size")
    parser.add_argument("--period", type=float, default=3.0, help="seconds per report")
    parser.add_argument("--workers", type=int, default=10, help="gateway coroutines")
    parser.add_argument("--chunk", type=int, default=500, help="reports per request")
    parser.add_argument(
        "--base-url", default="http://localhost:8000", help="API base URL"
    )
    parser.add_argument("--lat", type=float, default=50.4501, help="area center")
    parser.add_argument("--lon", type=float, default=30.5234, help="area center")
    parser.add_argument("--radius-km", type=float, default=8.0, help="area radius")
    parser.add_argument(
        "--duration", type=float, default=0.0, help="seconds to run (0 = until Ctrl+C)"
    )
    parser.add_argument("--seed", type=int, default=None, help="repeatable runs")
    options = parser.parse_args(argv)
    if options.devices < 1 or options.workers < 1 or options.period <= 0:
        parser.error("--devices and --workers must be >= 1, --period > 0")
    if not 1 <= options.chunk <= MAX_CHUNK:
        parser.error(f"--chunk must be between 1 and {MAX_CHUNK}")
    if options.radius_km <= 0:
        parser.error("--radius-km must be > 0")
    return options


def main(argv: Sequence[str] | None = None) -> None:
    options = parse_args(argv)
    meter = Meter()
    started = time.monotonic()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(simulate(options, meter))
    elapsed = time.monotonic() - started
    print("summary: " + meter.line(elapsed, meter.reported / max(elapsed, 1e-9)))


if __name__ == "__main__":
    main()
