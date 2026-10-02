"""Request-id correlation, security headers, and the audit-log tap.

Kept as plain ASGI middleware (not FastAPI dependencies) because these apply
to literally every response, including error responses the exception
handlers produce — a dependency wouldn't run if a route never matched.
"""
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.monotonic()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["x-response-time-ms"] = f"{(time.monotonic() - start) * 1000:.1f}"
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defense-in-depth headers. The frontend is a separate SPA origin, so
    there's no server-rendered HTML here to protect with a strict CSP of its
    own — these headers protect the API responses themselves (e.g. against a
    browser being tricked into rendering a JSON error body as HTML)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        response.headers["Cache-Control"] = "no-store"
        return response
