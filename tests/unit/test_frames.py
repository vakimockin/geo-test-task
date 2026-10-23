import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from geotrack.live import frames
from geotrack.telemetry.model import AppliedPosition, ZoneHit

MOMENT = datetime(2026, 10, 23, 9, 30, 15, 250_000, tzinfo=UTC)


def test_bus_rows_round_trip() -> None:
    applied = [AppliedPosition("unit-1", 50.45, 30.52, MOMENT)]
    rows = frames.unpack_rows(frames.pack_rows(applied))
    assert rows == [("unit-1", 50.45, 30.52, frames.epoch_ms(MOMENT))]


@pytest.mark.parametrize(
    "raw",
    ['{"a": 1}', "[[1, 2, 3]]", '[["d", "x", 1, 1]]', '[["d", NaN, 1, 1]]', "nope"],
)
def test_unpack_rejects_malformed_payloads(raw: str) -> None:
    with pytest.raises(ValueError):
        frames.unpack_rows(raw)


def test_position_item_and_frame_are_valid_json() -> None:
    item = frames.position_item("unit-1", 1.5, -2.5, frames.epoch_ms(MOMENT))
    frame = json.loads(frames.positions([item, item]))
    assert frame["kind"] == "positions"
    assert frame["items"][0] == {
        "device_id": "unit-1",
        "latitude": 1.5,
        "longitude": -2.5,
        "reported_at": "2026-10-23T09:30:15.250000+00:00",
    }
    assert json.loads(frames.positions([])) == {"kind": "positions", "items": []}


def test_alert_frame_lists_hits() -> None:
    hit = ZoneHit("alice", UUID(int=7), "unit-9", 50.0, 30.0, MOMENT)
    frame = json.loads(frames.alert([hit]))
    assert frame == {
        "kind": "alert",
        "items": [
            {
                "geozone_id": str(UUID(int=7)),
                "device_id": "unit-9",
                "latitude": 50.0,
                "longitude": 30.0,
                "reported_at": MOMENT.isoformat(),
            }
        ],
    }


def test_control_frames() -> None:
    assert json.loads(frames.hello("alice", 1.0)) == {
        "kind": "hello",
        "user": "alice",
        "push_interval": 1.0,
    }
    assert json.loads(frames.error("bad"))["detail"] == "bad"
    assert json.loads(frames.gone(["a", "b"]))["device_ids"] == ["a", "b"]
    changed = json.loads(frames.geozones_changed("deleted", "z1"))
    assert changed == {"kind": "geozones_changed", "action": "deleted", "id": "z1"}
