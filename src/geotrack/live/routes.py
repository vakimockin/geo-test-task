import asyncio
import contextlib
import json
import logging
from typing import Annotated

from fastapi import APIRouter, Query, WebSocket, status

from geotrack.geo import Viewport
from geotrack.live import frames
from geotrack.live.client import LiveClient
from geotrack.runtime import current_runtime
from geotrack.security import parse_user_id

log = logging.getLogger(__name__)

router = APIRouter(tags=["live"])


@router.websocket("/ws/live")
async def live_session(
    websocket: WebSocket,
    user: Annotated[str | None, Query(description="Caller's user id")] = None,
) -> None:
    """Live positions and geozone alerts for one session of a user."""
    user_id = parse_user_id(user)
    if user_id is None:
        # Closing before accept turns into an HTTP 403 handshake response.
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    runtime = current_runtime(websocket)
    settings = runtime.settings
    await websocket.accept()
    client = LiveClient(
        user_id,
        websocket,
        runtime.hub.cache,
        push_interval=settings.live_push_interval,
        frame_items=settings.live_frame_items,
        backlog=settings.live_control_backlog,
    )
    client.push(frames.hello(user_id, settings.live_push_interval))
    runtime.hub.attach(client)
    sender = asyncio.create_task(client.run(), name=f"live-send-{user_id}")
    reader = asyncio.create_task(_read_commands(websocket, client))
    try:
        done, _ = await asyncio.wait(
            {sender, reader}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            if not task.cancelled() and (problem := task.exception()) is not None:
                log.debug("live session of %s ended: %r", user_id, problem)
    finally:
        runtime.hub.detach(client)
        client.stop()
        for task in (sender, reader):
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(sender, reader, return_exceptions=True)


async def _read_commands(websocket: WebSocket, client: LiveClient) -> None:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        text = message.get("text")
        if text is None:
            client.push(frames.error("only text frames are supported"))
            continue
        try:
            client.watch(parse_command(text))
        except ValueError as exc:
            client.push(frames.error(str(exc)))


def parse_command(text: str) -> Viewport | None:
    try:
        command = json.loads(text)
    except ValueError:
        raise ValueError("commands must be JSON") from None
    if not isinstance(command, dict) or set(command) != {"viewport"}:
        raise ValueError('expected {"viewport": [west, south, east, north] | null}')
    raw = command["viewport"]
    return None if raw is None else Viewport.parse(raw)
