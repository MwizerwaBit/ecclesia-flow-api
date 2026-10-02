"""Database engines and the tenant-scoped session dependency.

Two request-serving roles, matching the two frontend access paths
(docs/database-design.md §8.2):

- ``app_tenant`` — every ordinary request. RLS applies in full; the session
  variable ``app.tenant_id`` (and ``app.user_id``, for the self-visibility
  carve-out on ``tenant_memberships``) is set from the *signed JWT*, never
  from a client-supplied header or body field.
- ``app_platform`` — the platform-admin service only. BYPASSRLS. Every
  dependency that hands out this session also requires
  ``require_platform_admin`` upstream and the caller is responsible for an
  explicit ``audit_logs`` write.

A third, superuser-ish engine (``admin``) exists only for Alembic and the
seed script — it is never wired into a FastAPI dependency.
"""
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


def _make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)


_settings = get_settings()
tenant_engine = _make_engine(_settings.database_url_app)
platform_engine = _make_engine(_settings.database_url_platform)
admin_engine = _make_engine(_settings.database_url)

TenantSessionLocal = async_sessionmaker(tenant_engine, expire_on_commit=False)
PlatformSessionLocal = async_sessionmaker(platform_engine, expire_on_commit=False)
AdminSessionLocal = async_sessionmaker(admin_engine, expire_on_commit=False)


async def get_admin_db() -> AsyncGenerator[AsyncSession, None]:
    """Superuser session — migrations/seed scripts ONLY, never a FastAPI route."""
    async with AdminSessionLocal() as session:
        yield session


@asynccontextmanager
async def tenant_session(tenant_id: str, user_id: str | None = None) -> AsyncGenerator[AsyncSession, None]:
    """Open a transaction on the ``app_tenant`` role with ``app.tenant_id``
    (and optionally ``app.user_id``) pinned for that transaction only.

    ``set_config(..., true)`` scopes to the current transaction, so a pooled
    connection can never leak one request's tenant context into the next —
    this is the one invariant the whole isolation model rests on.
    """
    async with TenantSessionLocal() as session:
        async with session.begin():
            await session.execute(text("select set_config('app.tenant_id', :v, true)"), {"v": tenant_id})
            if user_id:
                await session.execute(text("select set_config('app.user_id', :v, true)"), {"v": user_id})
            yield session


@asynccontextmanager
async def platform_session() -> AsyncGenerator[AsyncSession, None]:
    async with PlatformSessionLocal() as session:
        async with session.begin():
            yield session


@asynccontextmanager
async def self_scoped_session(user_id: str) -> AsyncGenerator[AsyncSession, None]:
    """``app.user_id`` set, ``app.tenant_id`` deliberately left unset.

    For the one query that's legitimately cross-tenant for an ordinary user:
    "which churches do I belong to" (login, and the tenant-switcher). The
    ``tenant_memberships`` RLS policy's self-visibility clause
    (``user_id = current_setting('app.user_id')``) is what makes this safe —
    every OTHER tenant-scoped table still filters to nothing, since
    ``app.tenant_id`` is unset.
    """
    async with TenantSessionLocal() as session:
        async with session.begin():
            await session.execute(text("select set_config('app.user_id', :v, true)"), {"v": user_id})
            yield session


@asynccontextmanager
async def unscoped_tenant_session() -> AsyncGenerator[AsyncSession, None]:
    """``app_tenant`` role but with NO ``app.tenant_id`` set.

    RLS policies compare against ``current_setting('app.tenant_id', true)``
    with the ``true`` "missing_ok" flag, so an unset variable means every
    tenant-scoped row is filtered out (the policy's equality check becomes
    ``tenant_id = NULL`` which matches nothing) — safe-by-default for the one
    legitimate pre-tenant-context use case: creating a brand new organization
    during registration, where ``app.tenant_id`` is set manually mid-transaction
    the moment the new org's id exists (see modules/tenant/service.py).
    """
    async with TenantSessionLocal() as session:
        async with session.begin():
            yield session
