"""The FastAPI dependency chain every protected route is built from:

    claims = Depends(get_current_claims)       # who is this, per the signed JWT
    db     = Depends(get_tenant_db)            # a transaction scoped to their tenant
    _      = Depends(require_permission(...))  # RBAC
    _      = Depends(require_unit_scope(...))  # ABAC, where the resource has a unit_id

Nothing here ever reads a tenant id, role, or permission list from a request
header, query param, or body — only from the verified JWT.
"""
from collections.abc import AsyncGenerator, Callable
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import platform_session, tenant_session, unscoped_tenant_session
from app.core.exceptions import ForbiddenError, StepUpRequiredError, UnauthorizedError
from app.core.security import AccessTokenClaims, TokenError, TokenType, decode_token


async def get_current_claims(
    authorization: Annotated[str | None, Header()] = None,
) -> AccessTokenClaims:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("Missing or malformed Authorization header")
    raw = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(raw)
    except TokenError as exc:
        raise UnauthorizedError(str(exc)) from exc
    if payload.get("token_type") != TokenType.ACCESS.value:
        raise UnauthorizedError("Not an access token")
    return AccessTokenClaims(**payload)


CurrentClaims = Annotated[AccessTokenClaims, Depends(get_current_claims)]


async def get_tenant_db(claims: CurrentClaims) -> AsyncGenerator[AsyncSession, None]:
    if not claims.tenant_id:
        raise ForbiddenError("No active church selected for this session")
    async with tenant_session(claims.tenant_id, claims.sub) as session:
        yield session


TenantDb = Annotated[AsyncSession, Depends(get_tenant_db)]


async def get_pre_tenant_db() -> AsyncGenerator[AsyncSession, None]:
    """For the handful of endpoints that run before a tenant context exists
    (registration, the public church directory lookup)."""
    async with unscoped_tenant_session() as session:
        yield session


PreTenantDb = Annotated[AsyncSession, Depends(get_pre_tenant_db)]


async def require_platform_admin(claims: CurrentClaims) -> AccessTokenClaims:
    if not claims.is_platform_admin:
        raise ForbiddenError("Platform admin access required")
    return claims


PlatformClaims = Annotated[AccessTokenClaims, Depends(require_platform_admin)]


async def get_platform_db(_: PlatformClaims) -> AsyncGenerator[AsyncSession, None]:
    async with platform_session() as session:
        yield session


PlatformDb = Annotated[AsyncSession, Depends(get_platform_db)]


def require_platform_admin_level(level: str) -> Callable:
    async def _check(claims: PlatformClaims) -> AccessTokenClaims:
        if level == "full" and claims.platform_admin_level != "full":
            raise ForbiddenError("This action requires full platform admin access")
        return claims

    return _check


def require_permission(permission: str) -> Callable:
    """RBAC gate — mirrors the frontend's `can()` in useRole.ts exactly, so a
    screen gated client-side by one permission string is gated by the same
    string server-side."""

    async def _check(claims: CurrentClaims) -> AccessTokenClaims:
        if claims.is_platform_admin:
            return claims
        if permission not in claims.permissions:
            raise ForbiddenError(f"Missing permission: {permission}")
        return claims

    return _check


def require_any_permission(*permissions: str) -> Callable:
    async def _check(claims: CurrentClaims) -> AccessTokenClaims:
        if claims.is_platform_admin:
            return claims
        if not any(p in claims.permissions for p in permissions):
            raise ForbiddenError(f"Missing one of permissions: {', '.join(permissions)}")
        return claims

    return _check


async def require_step_up(
    claims: CurrentClaims,
    x_step_up_token: Annotated[str | None, Header()] = None,
) -> None:
    """Sensitive actions (leadership transfer, data erasure approval, MFA
    disable) require a second, short-lived token proving the user just
    re-entered their MFA code — not merely that their 15-minute access token
    hasn't expired yet."""
    if not x_step_up_token:
        raise StepUpRequiredError("This action requires MFA step-up re-authentication")
    try:
        payload = decode_token(x_step_up_token)
    except TokenError as exc:
        raise StepUpRequiredError("Step-up token is invalid or expired") from exc
    if payload.get("token_type") != TokenType.STEP_UP.value:
        raise StepUpRequiredError("Not a step-up token")
    if payload.get("sub") != claims.sub:
        raise StepUpRequiredError("Step-up token does not match the current session")
