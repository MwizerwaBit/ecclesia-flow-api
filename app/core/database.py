"""Prisma clients and the tenant-scoped transaction helper.

Two long-lived, request-serving clients, matching the two frontend access
paths (docs/database-design.md §8.2):

- ``tenant_client`` — every ordinary request, connected as ``app_tenant``.
  RLS applies in full; the session variable ``app.tenant_id`` (and
  ``app.user_id``, for the self-visibility carve-out on
  ``tenant_memberships``) is set from the *signed JWT*, never from a
  client-supplied header or body field.
- ``platform_client`` — the platform-admin service only, connected as
  ``app_platform``. BYPASSRLS. Every dependency that hands out a transaction
  on this client also requires ``require_platform_admin`` upstream, and the
  caller is responsible for an explicit ``audit_logs`` write.

Both are plain ``Prisma()`` instances pointed at a non-default datasource
URL and connected once at process startup (see ``app/main.py``'s lifespan).
A transaction (``client.tx()``) is Prisma's unit that can mix raw SQL
(``set_config``) and generated-model queries (``tx.user.find_many(...)``)
against the SAME underlying connection — the equivalent of the SQLAlchemy
``AsyncSession`` this module used to wrap.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from prisma import Prisma

from app.core.config import get_settings

_settings = get_settings()

tenant_client = Prisma(datasource={"url": _settings.database_url_app})
platform_client = Prisma(datasource={"url": _settings.database_url_platform})


async def connect_clients() -> None:
    await tenant_client.connect()
    await platform_client.connect()


async def disconnect_clients() -> None:
    if tenant_client.is_connected():
        await tenant_client.disconnect()
    if platform_client.is_connected():
        await platform_client.disconnect()


#: ``app.unit_scope`` value for a session that covers the whole church.
WHOLE_CHURCH = "all"


async def set_tenant_context(db: Prisma, tenant_id: str, *, unit_scope: str | None = WHOLE_CHURCH) -> None:
    """Pin the tenant (and unit scope) for the rest of this transaction.

    ``app.unit_scope`` is ``'all'`` or the uuid of the unit a branch-scoped
    session is limited to; the unit-scope RLS policies on people data match
    nothing when it is unset, so every place that sets ``app.tenant_id``
    sets this too — through here."""
    await db.execute_raw("select set_config('app.tenant_id', $1, true)", tenant_id)
    await db.execute_raw("select set_config('app.unit_scope', $1, true)", unit_scope or WHOLE_CHURCH)


async def clear_tenant_context(db: Prisma) -> None:
    await db.execute_raw("select set_config('app.tenant_id', '', true)")
    await db.execute_raw("select set_config('app.unit_scope', '', true)")


@asynccontextmanager
async def tenant_session(
    tenant_id: str, user_id: str | None = None, unit_scope_id: str | None = None
) -> AsyncGenerator[Prisma, None]:
    """Open a transaction on the ``app_tenant`` role with ``app.tenant_id``,
    ``app.unit_scope`` (and optionally ``app.user_id``) pinned for that
    transaction only.

    ``set_config(..., true)`` scopes to the current transaction, so a pooled
    connection can never leak one request's tenant context into the next —
    this is the one invariant the whole isolation model rests on. (It does
    NOT reset to NULL afterward, only to an empty string — see
    docs/SECURITY_NOTES.md §1 for why every RLS policy guards against that
    with ``current_tenant_id()``/``current_app_user_id()`` instead of a raw
    cast.)
    """
    async with tenant_client.tx() as tx:
        await set_tenant_context(tx, tenant_id, unit_scope=unit_scope_id)
        if user_id:
            await tx.execute_raw("select set_config('app.user_id', $1, true)", user_id)
        yield tx


@asynccontextmanager
async def platform_session() -> AsyncGenerator[Prisma, None]:
    async with platform_client.tx() as tx:
        yield tx


@asynccontextmanager
async def self_scoped_session(user_id: str) -> AsyncGenerator[Prisma, None]:
    """``app.user_id`` set, ``app.tenant_id`` deliberately left unset.

    For the one query that's legitimately cross-tenant for an ordinary user:
    "which churches do I belong to" (login, and the tenant-switcher). The
    ``tenant_memberships`` RLS policy's self-visibility clause
    (``user_id = current_app_user_id()``) is what makes this safe — every
    OTHER tenant-scoped table still filters to nothing, since
    ``app.tenant_id`` is unset.
    """
    async with tenant_client.tx() as tx:
        await tx.execute_raw("select set_config('app.user_id', $1, true)", user_id)
        yield tx


@asynccontextmanager
async def unscoped_tenant_session() -> AsyncGenerator[Prisma, None]:
    """``app_tenant`` role but with NO ``app.tenant_id``/``app.user_id`` set.

    RLS policies compare against ``current_tenant_id()``, which is NULL when
    unset, so every tenant-scoped row is filtered out — safe-by-default for
    the one legitimate pre-tenant-context use case: creating a brand new
    organization during registration. ``organizations`` itself carries no
    tenant_id and isn't RLS-protected, so the insert succeeds; the
    hierarchy_units insert that follows it switches this same transaction to
    the new tenant's context first (see modules/tenant/service.py).
    """
    async with tenant_client.tx() as tx:
        yield tx
