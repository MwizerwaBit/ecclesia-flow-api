"""Request-id correlation, security headers, and the audit-log tap.

Kept as plain ASGI middleware (not FastAPI dependencies) because these apply
to literally every response, including error responses the exception
handlers produce — a dependency wouldn't run if a route never matched.
"""

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings

_access_log = logging.getLogger("ecclesia_flow.access")
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Correlation id + one access-log line per request (no bodies, query
    values removed)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get("x-request-id", "")
        # A caller-supplied id is accepted only if it's short and plain, so it
        # can't be used to inject content into logs.
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = round((time.monotonic() - start) * 1000, 1)
        response.headers["x-request-id"] = request_id
        response.headers["x-response-time-ms"] = f"{elapsed_ms:.1f}"
        _access_log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "query_keys": sorted(request.query_params.keys()),
                "status": response.status_code,
                "duration_ms": elapsed_ms,
            },
        )
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
        if request.url.path.startswith(get_settings().media_public_url_prefix + "/"):
            # Uploaded files: never executable, never framed, even if one slips past the type check.
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; sandbox"
        return response
