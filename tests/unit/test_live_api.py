import json
from typing import Any

import pytest
from starlette.testclient import TestClient, WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from tests.fakes import StubRuntimeParts
from tests.timeouts import receive_frame


def until_kind(ws: WebSocketTestSession, kind: str, limit: int = 20) -> dict[str, Any]:
    for _ in range(limit):
        frame = receive_frame(ws)
        if frame["kind"] == kind:
            return frame
    raise AssertionError(f"no {kind!r} frame among {limit}")


def test_requires_a_valid_user(client: TestClient) -> None:
    for url in ("/ws/live", "/ws/live?user=a%20b"):
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect(url):
                pass
        assert closed.value.code == 1008


def test_hello_then_positions_snapshot(
    client: TestClient, parts: StubRuntimeParts
) -> None:
    parts.runtime.hub.positions([("unit-1", 50.45, 30.52, 1_000)])
    with client.websocket_connect("/ws/live?user=alice") as ws:
        hello = receive_frame(ws)
        assert hello["kind"] == "hello" and hello["user"] == "alice"
        snapshot = until_kind(ws, "positions")
        assert [i["device_id"] for i in snapshot["items"]] == ["unit-1"]


def test_every_session_of_the_user_gets_its_frames(
    client: TestClient, parts: StubRuntimeParts
) -> None:
    hub = parts.runtime.hub
    with (
        client.websocket_connect("/ws/live?user=alice") as laptop,
        client.websocket_connect("/ws/live?user=alice") as phone,
        client.websocket_connect("/ws/live?user=bob") as other,
    ):
        for ws in (laptop, phone, other):
            until_kind(ws, "hello")
        assert hub.session_count("alice") == 2
        hub.to_user("alice", '{"kind":"alert","items":[]}')
        hub.to_user("bob", '{"kind":"geozones_changed","action":"created","id":"z"}')
        assert until_kind(laptop, "alert")["items"] == []
        assert until_kind(phone, "alert")["items"] == []
        assert until_kind(other, "geozones_changed")["id"] == "z"
    assert hub.session_count() == 0


def test_viewport_command_filters_positions(
    client: TestClient, parts: StubRuntimeParts
) -> None:
    hub = parts.runtime.hub
    with client.websocket_connect("/ws/live?user=alice") as ws:
        until_kind(ws, "hello")
        ws.send_text(json.dumps({"viewport": [30.4, 50.4, 30.6, 50.5]}))
        hub.positions([("inside", 50.45, 30.52, 1), ("outside", 10.0, 10.0, 1)])
        seen: set[str] = set()
        for _ in range(5):
            frame = until_kind(ws, "positions")
            seen |= {item["device_id"] for item in frame["items"]}
            if "inside" in seen:
                break
        assert seen == {"inside"}


@pytest.mark.parametrize(
    "command",
    [
        "hello",
        "{}",
        '{"viewport": [1, 2, 3]}',
        '{"viewport": [0, 10, 1, 5]}',
        '{"viewport": ["a", 0, 1, 1]}',
        '{"viewport": null, "extra": 1}',
    ],
)
def test_bad_commands_get_error_frames(client: TestClient, command: str) -> None:
    with client.websocket_connect("/ws/live?user=alice") as ws:
        until_kind(ws, "hello")
        ws.send_text(command)
        assert until_kind(ws, "error")["detail"]
        ws.send_text('{"viewport": null}')
        ws.send_bytes(b"\x00")
        assert "text" in until_kind(ws, "error")["detail"]
