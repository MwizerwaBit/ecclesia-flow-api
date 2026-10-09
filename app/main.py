from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api import api_router
from app.core.body_limit import RequestSizeLimitMiddleware
from app.core.config import get_settings
from app.core.database import connect_clients, disconnect_clients, tenant_client
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIdMiddleware, SecurityHeadersMiddleware
from app.core.rate_limit import limiter


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    await connect_clients()
    try:
        yield
    finally:
        await disconnect_clients()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.is_production and (problems := settings.production_problems()):
        raise RuntimeError("Refusing to start in production with development settings: " + ", ".join(problems))
    app = FastAPI(
        title="EcclesiaFlow API",
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=_lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    register_exception_handlers(app)

    app.add_middleware(SlowAPIMiddleware)
    # Inside the header/request-id middleware so a 413 still carries them.
    app.add_middleware(RequestSizeLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Step-Up-Token",
            "X-Request-Id",
            "X-Requested-With",
            "X-Refresh-Token-Transport",
        ],
        # Pagination metadata and correlation id must be readable by the SPA.
        expose_headers=["X-Total-Count", "X-Page-Limit", "X-Page-Offset", "Link", "X-Request-Id"],
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        """Liveness: the process is up."""
        return {"status": "ok"}

    @app.get("/health/ready", tags=["meta"])
    async def ready() -> JSONResponse:
        """Readiness: the database answers. Load balancers route traffic only
        to instances that pass this."""
        try:
            await tenant_client.query_raw("select 1")
        except Exception:  # noqa: BLE001 — any failure means "not ready"
            return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
        return JSONResponse({"status": "ok", "database": "up"})

    app.include_router(api_router)

    # Local-disk stand-in for the real object store (see docs/SECURITY_NOTES.md
    # — this serves every uploaded file at a permanent, unauthenticated URL,
    # not the presigned/time-limited access docs/database-design.md calls
    # for; fine for avatars/logos/event banners, not a substitute for real
    # object storage before anything more sensitive is uploaded through it).
    media_dir = Path(settings.media_local_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    app.mount(settings.media_public_url_prefix, StaticFiles(directory=media_dir), name="media")

    return app


async def _rate_limit_handler(request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=429,
        content={"error": {"code": "rate_limited", "message": "Too many requests. Please try again shortly."}},
    )


app = create_app()
