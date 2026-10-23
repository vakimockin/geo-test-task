import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as NotYet
from typing import Any

from starlette.testclient import WebSocketTestSession

READERS = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ws-reader")


def receive_frame(ws: WebSocketTestSession, seconds: float = 5.0) -> dict[str, Any]:
    pending = READERS.submit(ws.receive_text)
    try:
        frame: dict[str, Any] = json.loads(pending.result(timeout=seconds))
    except NotYet:
        raise AssertionError(f"websocket stayed silent for {seconds}s") from None
    return frame
