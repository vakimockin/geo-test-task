import json
from typing import Any, Literal

ZoneAction = Literal["created", "updated", "deleted"]


def _dump(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"))


def geozones_changed(action: ZoneAction, geozone_id: str) -> str:
    return _dump({"kind": "geozones_changed", "action": action, "id": geozone_id})
