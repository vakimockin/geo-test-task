import math
import random

import httpx
import pytest

import generator
from generator import Area, Vehicle

KYIV = Area(50.4501, 30.5234, 8_000)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    north, east = generator.offset_to(lat1, lon1, lat2, lon2)
    return math.hypot(north, east)


def test_fleet_has_unique_ids_inside_the_area() -> None:
    fleet = generator.spawn_fleet(500, KYIV, random.Random(1))
    assert len({v.device_id for v in fleet}) == 500
    for vehicle in fleet:
        assert distance_m(KYIV.lat, KYIV.lon, vehicle.lat, vehicle.lon) <= 8_050
        assert 4.0 <= vehicle.speed_mps <= 16.0


def test_drive_moves_toward_the_goal_by_speed_times_time() -> None:
    rng = random.Random(2)
    goal = generator.shift(KYIV.lat, KYIV.lon, 5_000, 0)
    car = Vehicle("v", KYIV.lat, KYIV.lon, goal[0], goal[1], speed_mps=10)
    before = distance_m(car.lat, car.lon, *goal)
    generator.drive(car, 3.0, KYIV, rng)
    moved = distance_m(KYIV.lat, KYIV.lon, car.lat, car.lon)
    assert moved == pytest.approx(30.0, rel=0.02)
    assert distance_m(car.lat, car.lon, *goal) < before


def test_arrival_picks_a_new_goal() -> None:
    rng = random.Random(3)
    goal = generator.shift(KYIV.lat, KYIV.lon, 10, 0)
    car = Vehicle("v", KYIV.lat, KYIV.lon, goal[0], goal[1], speed_mps=10)
    generator.drive(car, 3.0, KYIV, rng)
    assert (car.lat, car.lon) == goal
    assert (car.goal_lat, car.goal_lon) != goal


def test_positions_drift_rather_than_jump() -> None:
    rng = random.Random(4)
    fleet = generator.spawn_fleet(200, KYIV, rng)
    before = [(v.lat, v.lon) for v in fleet]
    for vehicle in fleet:
        generator.drive(vehicle, 3.0, KYIV, rng)
    steps = [distance_m(*old, v.lat, v.lon) for old, v in zip(before, fleet)]
    assert all(0 < step <= 16.0 * 3 + 1 for step in steps)


def test_shift_wraps_longitude_and_clamps_latitude() -> None:
    lat, lon = generator.shift(0.0, 179.9999, 0, 100)
    assert -180 <= lon < -179.99
    lat, _ = generator.shift(89.9999, 0, 10_000, 0)
    assert lat == 90.0


def test_batch_body_matches_the_api() -> None:
    car = Vehicle("unit-00001", 50.123456789, 30.1, 0, 0, 5)
    body = generator.batch_body([car], "2026-10-23T10:00:00+00:00")
    assert body == {
        "positions": [
            {
                "device_id": "unit-00001",
                "latitude": 50.1234568,
                "longitude": 30.1,
                "timestamp": "2026-10-23T10:00:00+00:00",
            }
        ]
    }


def test_chunks() -> None:
    fleet = generator.spawn_fleet(7, KYIV, random.Random(5))
    assert [len(c) for c in generator.chunks(fleet, 3)] == [3, 3, 1]


@pytest.mark.parametrize(
    "header, expected", [("2", 2.0), ("-1", 0.0), ("999", 30.0), ("soon", 1.0)]
)
def test_retry_after(header: str, expected: float) -> None:
    response = httpx.Response(503, headers={"Retry-After": header})
    assert generator.retry_after(response) == expected
    assert generator.retry_after(httpx.Response(503)) == 1.0


def test_defaults_simulate_ten_thousand_devices() -> None:
    options = generator.parse_args([])
    assert (options.devices, options.period, options.chunk) == (10_000, 3.0, 500)


@pytest.mark.parametrize(
    "argv",
    [["--devices", "0"], ["--period", "0"], ["--chunk", "6000"], ["--radius-km", "0"]],
)
def test_rejects_bad_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        generator.parse_args(argv)


def test_meter_line() -> None:
    meter = generator.Meter(reported=10, queued=9, rejected=1)
    meter.latencies_ms.extend([5.0, 10.0, 20.0])
    text = meter.line(12.0, 3_300.0)
    assert "reported=10" in text and "rate=3,300/s" in text and "p50=10ms" in text
    meter.fail("HTTP 500: boom")
    assert "last_failure='HTTP 500: boom'" in meter.line(12.0, 1.0)
