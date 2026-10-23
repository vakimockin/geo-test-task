import asyncio
import contextlib
import json
from http import HTTPStatus

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from geotrack.problems import PROBLEM_MEDIA_TYPE, problem_body

# unread request bytes make the client's TCP stack drop the 413 on close
DRAIN_MAX_BYTES = 16 * 1024 * 1024
DRAIN_MAX_SECONDS = 5.0


class BodyLimitMiddleware:
    """Buffers request bodies up to ``max_bytes`` and answers 413 beyond that."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _declared_length(scope)
        if declared is not None and declared > self.max_bytes:
            await _discard(receive)
            await self._reject(send)
            return

        parts: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_bytes:
                if message.get("more_body", False):
                    await _discard(receive)
                await self._reject(send)
                return
            parts.append(chunk)
            if not message.get("more_body", False):
                break

        body = b"".join(parts)
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)

    async def _reject(self, send: Send) -> None:
        payload = json.dumps(
            problem_body(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "payload-too-large",
                f"request body is larger than {self.max_bytes} bytes",
            )
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "headers": [
                    (b"content-type", PROBLEM_MEDIA_TYPE.encode()),
                    (b"content-length", str(len(payload)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})


async def _discard(receive: Receive) -> None:
    async def drain() -> None:
        budget = DRAIN_MAX_BYTES
        while budget > 0:
            message = await receive()
            if message["type"] != "http.request":
                return
            budget -= len(message.get("body", b""))
            if not message.get("more_body", False):
                return

    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(drain(), DRAIN_MAX_SECONDS)


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None
