"""Request body size limit (DIF-05).

Plain ASGI rather than ``BaseHTTPMiddleware`` because it has to watch the
body as it streams: a declared ``Content-Length`` over the limit is refused
before anything is read, and a body without one (chunked transfer encoding)
is counted chunk by chunk and cut off the moment it passes the limit.

Limits (``app.core.config``):
  max_request_body_bytes   every request (JSON APIs) — 1 MB
  max_upload_body_bytes    multipart/form-data, i.e. file uploads — 11 MB,
                           justified by the 10 MB document/media cap plus
                           multipart overhead

The reverse proxy in front of the API should enforce the upload ceiling too
(docs/OPERATIONS.md), so oversized bodies never reach a worker at all.
"""

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings


class _BodyTooLarge(Exception):
    pass


def _limit_for(scope: Scope) -> int:
    settings = get_settings()
    for name, value in scope.get("headers", []):
        if name == b"content-type" and value.lower().startswith(b"multipart/form-data"):
            return settings.max_upload_body_bytes
    return settings.max_request_body_bytes


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _send_413(scope: Scope, send: Send, limit: int) -> None:
    request_id = scope.get("state", {}).get("request_id", "")
    body = json.dumps(
        {
            "error": {
                "code": "payload_too_large",
                "message": f"The request body is larger than the {limit // 1024} KB this endpoint accepts.",
                "requestId": request_id,
            }
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = _limit_for(scope)
        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            await _send_413(scope, send, limit)
            return

        received = 0
        exceeded = False
        replaced = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            # FastAPI turns any error while reading the body into its own 400;
            # once the limit was crossed, whatever response follows is
            # replaced by the 413.
            nonlocal replaced
            if exceeded:
                if not replaced:
                    replaced = True
                    await _send_413(scope, send, limit)
                return
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            if not replaced:
                replaced = True
                await _send_413(scope, send, limit)
