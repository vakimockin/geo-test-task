import json
import math
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from geotrack.telemetry.model import AppliedPosition, ZoneHit

ZoneAction = Literal["created", "updated", "deleted"]

# One bus row: device id, latitude, longitude, report time in epoch ms.
BusRow = tuple[str, float, float, int]


def _dump(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"))


def hello(user: str, push_interval: float) -> str:
    return _dump({"kind": "hello", "user": user, "push_interval": push_interval})


def error(detail: str) -> str:
    return _dump({"kind": "error", "detail": detail})


def geozones_changed(action: ZoneAction, geozone_id: str) -> str:
    return _dump({"kind": "geozones_changed", "action": action, "id": geozone_id})


def gone(device_ids: Sequence[str]) -> str:
    return _dump({"kind": "gone", "device_ids": list(device_ids)})


def alert(hits: Sequence[ZoneHit]) -> str:
    return _dump(
        {
            "kind": "alert",
            "items": [
                {
                    "geozone_id": str(hit.geozone_id),
                    "device_id": hit.device_id,
                    "latitude": hit.lat,
                    "longitude": hit.lon,
                    "reported_at": hit.reported_at.isoformat(),
                }
                for hit in hits
            ],
        }
    )


def position_item(device_id: str, lat: float, lon: float, at_ms: int) -> str:
    """Pre-encoded array element, cached once and reused for every client."""
    stamp = datetime.fromtimestamp(at_ms / 1000, UTC).isoformat()
    return _dump(
        {
            "device_id": device_id,
            "latitude": lat,
            "longitude": lon,
            "reported_at": stamp,
        }
    )


def positions(items: Iterable[str]) -> str:
    return '{"kind":"positions","items":[' + ",".join(items) + "]}"


def epoch_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def pack_rows(applied: Sequence[AppliedPosition]) -> str:
    return _dump(
        [[p.device_id, p.lat, p.lon, epoch_ms(p.reported_at)] for p in applied]
    )


def unpack_rows(raw: str | bytes) -> list[BusRow]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("position payload must be a JSON array")
    rows: list[BusRow] = []
    for entry in data:
        if not (isinstance(entry, list) and len(entry) == 4):
            raise ValueError("position rows must be [id, lat, lon, epoch_ms]")
        device_id, lat, lon, at_ms = entry
        lat, lon = float(lat), float(lon)
        if not (math.isfinite(lat) and math.isfinite(lon)):
            raise ValueError("position coordinates must be finite")
        rows.append((str(device_id), lat, lon, int(at_ms)))
    return rows
