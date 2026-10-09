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
from prisma import Prisma

from app.core.database import platform_session, tenant_session, unscoped_tenant_session
from app.core.exceptions import AppError, ForbiddenError, StepUpRequiredError, UnauthorizedError
from app.core.permissions import MFA_REQUIRED_PERMISSIONS
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


async def get_tenant_db(claims: CurrentClaims) -> AsyncGenerator[Prisma, None]:
    if not claims.tenant_id:
        raise ForbiddenError("No active church selected for this session")
    # The unit scope comes from the signed token too; _ensure_session_current
    # rejects the request if it no longer matches the membership.
    async with tenant_session(claims.tenant_id, claims.sub, claims.unit_scope_id) as tx:
        await _ensure_session_current(tx, claims)
        yield tx


async def _ensure_session_current(db: Prisma, claims: AccessTokenClaims) -> None:
    """Permissions and unit scope ride in the access token, which lives up to
    15 minutes. Re-checking the membership on every request means a suspended
    member, a deactivated account, or a role/scope change takes effect on the
    very next call instead of whenever the token happens to expire. A stale
    token gets 401 "session_stale"; the client refreshes and receives the
    current permissions."""
    if claims.membership_id is None:
        # Platform-admin impersonation tokens carry no membership; they are
        # short-lived and audited separately.
        return
    rows = await db.query_raw(
        """
        select tm.status, tm.role_id, tm.unit_scope_id, u.status as user_status,
               u.password_changed_at >= to_timestamp($4::bigint + 1) as password_changed_since
        from tenant_memberships tm join users u on u.id = tm.user_id
        where tm.id = $1::uuid and tm.user_id = $2::uuid and tm.tenant_id = $3::uuid
        """,
        claims.membership_id,
        claims.sub,
        claims.tenant_id,
        claims.iat,
    )
    if not rows:
        raise UnauthorizedError("This session is no longer valid", code="session_revoked")
    row = rows[0]
    if row["password_changed_since"]:
        # The password changed after this token was issued (e.g. a reset).
        # Tokens carry whole-second iat, so one issued within the same second
        # as the change survives at most until it expires (≤15 min); the
        # refresh tokens were all revoked by the change itself.
        raise UnauthorizedError("Your password changed — sign in again", code="session_revoked")
    if row["status"] != "active" or row["user_status"] != "active":
        raise UnauthorizedError("Your access to this church has been suspended", code="session_revoked")
    if str(row["role_id"]) != str(claims.role_id) or (row["unit_scope_id"] or None) != (claims.unit_scope_id or None):
        raise UnauthorizedError("Your role has changed — refresh your session", code="session_stale")


TenantDb = Annotated[Prisma, Depends(get_tenant_db)]


class OrgInactiveError(AppError):
    status_code = 402
    code = "org_inactive"


async def require_active_org(claims: CurrentClaims, db: TenantDb) -> None:
    """Business data is for churches that are live. A pending church can sign
    in, finish onboarding and pay (those routers don't use this guard); a
    suspended or closed one can still reach billing and support, nothing else.
    Platform admins impersonating a church are let through to help it."""
    if claims.is_platform_admin:
        return
    rows = await db.query_raw("select status from organizations where id = $1::uuid", claims.tenant_id)
    status = rows[0]["status"] if rows else None
    if status in ("active", "trial"):
        return
    if status == "pending":
        raise OrgInactiveError("Your church isn't active yet — complete payment or wait for activation")
    if status == "suspended":
        raise ForbiddenError("This church is suspended. Contact EcclesiaFlow support.", code="org_suspended")
    raise ForbiddenError("This church's account is closed", code="org_closed")


async def get_pre_tenant_db() -> AsyncGenerator[Prisma, None]:
    """For the handful of endpoints that run before a tenant context exists
    (registration, the public church directory lookup)."""
    async with unscoped_tenant_session() as tx:
        yield tx


PreTenantDb = Annotated[Prisma, Depends(get_pre_tenant_db)]


async def require_platform_admin(claims: CurrentClaims) -> AccessTokenClaims:
    if not claims.is_platform_admin:
        raise ForbiddenError("Platform admin access required")
    return claims


PlatformClaims = Annotated[AccessTokenClaims, Depends(require_platform_admin)]


async def get_platform_db(_: PlatformClaims) -> AsyncGenerator[Prisma, None]:
    async with platform_session() as tx:
        yield tx


PlatformDb = Annotated[Prisma, Depends(get_platform_db)]


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
        if permission in MFA_REQUIRED_PERMISSIONS and not claims.mfa_verified:
            raise ForbiddenError("Turn on two-step sign-in and sign in with it to do this", code="mfa_required")
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
