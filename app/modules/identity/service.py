import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.security import (
    TokenError,
    TokenType,
    create_access_token,
    create_mfa_challenge_token,
    create_step_up_token,
    decode_token,
    decrypt_mfa_secret,
    encrypt_mfa_secret,
    generate_backup_codes,
    generate_totp_secret,
    hash_backup_code,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    totp_provisioning_uri,
    verify_password,
    verify_totp,
)
from app.modules.audit.service import write_audit
from app.modules.identity import repository
from app.modules.identity.models import User
from app.modules.identity.schemas import (
    MembershipSummary,
    MfaChallengeResponse,
    MfaSetupResponse,
    RegisterRequest,
    SessionResponse,
    StepUpResponse,
    UserPublic,
)
from app.modules.rbac import repository as rbac_repository
from app.modules.rbac.models import SYSTEM_ROLE_NAMES
from app.modules.rbac.service import resolve_permissions
from app.modules.tenant.service import create_organization_with_root_unit


async def _set_user_scope(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(text("select set_config('app.user_id', :v, true)"), {"v": str(user_id)})


async def _membership_summaries(db: AsyncSession, user_id: uuid.UUID) -> list[MembershipSummary]:
    await _set_user_scope(db, user_id)
    rows = await repository.list_memberships_for_user(db, user_id)
    return [
        MembershipSummary(
            id=r["id"],
            tenant_id=r["tenant_id"],
            tenant_name=r["tenant_name"],
            role_name=r["role_name"],
            is_primary=r["is_primary"],
            is_leader=r["is_leader"],
            unit_scope_id=r["unit_scope_id"],
        )
        for r in rows
    ]


async def _issue_tokens_for_membership(
    db: AsyncSession,
    *,
    user: User,
    membership_summary: MembershipSummary | None,
    all_memberships: list[MembershipSummary],
    user_agent: str | None,
    ip_address: str | None,
    reuse_family_id: uuid.UUID | None = None,
) -> SessionResponse:
    """Mints a fresh access token AND a fresh refresh token.

    ``reuse_family_id`` is set only by the refresh-rotation path: the new
    refresh token stays part of the SAME rotation chain as the one it
    replaces, so a reuse of any earlier token in the chain still burns the
    whole family, not just the newest link."""
    permissions: list[str] = []
    role_id: uuid.UUID | None = None
    if membership_summary is not None:
        membership = await repository.get_membership(db, membership_summary.id)
        role_id = membership.role_id
        permissions = await resolve_permissions(db, role_id)

    access_token = create_access_token(
        user_id=user.id,
        membership_id=membership_summary.id if membership_summary else None,
        tenant_id=membership_summary.tenant_id if membership_summary else None,
        role_id=role_id,
        permissions=permissions,
        unit_scope_id=membership_summary.unit_scope_id if membership_summary else None,
        is_platform_admin=user.is_platform_admin,
        platform_admin_level=user.platform_admin_level,
        mfa_verified=True,
    )

    settings = get_settings()
    raw_refresh = new_refresh_token()
    new_token_row = await repository.store_refresh_token(
        db,
        user_id=user.id,
        membership_id=membership_summary.id if membership_summary else None,
        token_hash=hash_refresh_token(raw_refresh),
        family_id=reuse_family_id or uuid.uuid4(),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
        user_agent=user_agent,
        ip_address=ip_address,
    )

    return SessionResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        user=UserPublic.model_validate(user),
        active_membership=membership_summary,
        memberships=all_memberships,
        permissions=permissions,
        role=SYSTEM_ROLE_NAMES.get(role_id) if role_id else None,
        unit_scope_id=membership_summary.unit_scope_id if membership_summary else None,
    ), new_token_row


async def register_church_leader(
    db: AsyncSession, payload: RegisterRequest, *, user_agent: str | None, ip_address: str | None
) -> SessionResponse:
    """Onboarding's first step, per plan.md Phase 1 + the frontend brief:
    "onboarding begins with registering the church leader." One transaction:
    org + root hierarchy unit + user + membership (is_primary, is_leader)."""
    existing = await repository.get_user_by_email(db, payload.email)
    if existing is not None:
        raise ForbiddenError("An account with this email already exists")

    org, _root_unit_id = await create_organization_with_root_unit(
        db,
        legal_name=payload.church_name,
        display_name=payload.church_name,
        country=payload.country,
        currency=payload.currency,
        timezone_name=payload.timezone,
    )

    user = await repository.create_user(
        db,
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        first_name=payload.first_name,
        last_name=payload.last_name,
    )

    staff_role = await rbac_repository.get_system_role_by_name(db, "staff")
    if staff_role is None:
        raise RuntimeError("System roles are not seeded — run the initial migration/seed script")

    membership = await repository.create_membership(
        db,
        user_id=user.id,
        tenant_id=org.id,
        role_id=staff_role.id,
        is_primary=True,
        is_leader=True,
    )

    await write_audit(
        db,
        tenant_id=org.id,
        actor_user_id=user.id,
        action="organization.registered",
        resource_type="organization",
        resource_id=org.id,
        ip_address=ip_address,
    )

    summary = MembershipSummary(
        id=membership.id,
        tenant_id=org.id,
        tenant_name=org.display_name,
        role_name="staff",
        is_primary=True,
        is_leader=True,
        unit_scope_id=None,
    )
    session, _ = await _issue_tokens_for_membership(
        db,
        user=user,
        membership_summary=summary,
        all_memberships=[summary],
        user_agent=user_agent,
        ip_address=ip_address,
    )
    return session


async def authenticate(
    db: AsyncSession, *, email: str, password: str, user_agent: str | None, ip_address: str | None
) -> SessionResponse | MfaChallengeResponse:
    user = await repository.get_user_by_email(db, email.lower())
    # Constant-shape failure: a wrong password and a nonexistent email return
    # the identical error, so the endpoint can't be used to enumerate accounts.
    if user is None or user.password_hash is None or not verify_password(password, user.password_hash):
        raise UnauthorizedError("Incorrect email or password")
    if user.status != "active":
        raise ForbiddenError("This account is not active")

    if user.mfa_enabled:
        return MfaChallengeResponse(challenge_token=create_mfa_challenge_token(user_id=user.id))

    memberships = await _membership_summaries(db, user.id)
    primary = next((m for m in memberships if m.is_primary), memberships[0] if memberships else None)
    await repository.touch_last_login(db, user.id)
    session, _ = await _issue_tokens_for_membership(
        db,
        user=user,
        membership_summary=primary,
        all_memberships=memberships,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    return session


async def complete_mfa_challenge(
    db: AsyncSession, *, challenge_token: str, code: str, user_agent: str | None, ip_address: str | None
) -> SessionResponse:
    try:
        payload = decode_token(challenge_token)
    except TokenError as exc:
        raise UnauthorizedError(str(exc)) from exc
    if payload.get("token_type") != TokenType.MFA_CHALLENGE.value:
        raise UnauthorizedError("Not an MFA challenge token")

    user = await repository.get_user_by_id(db, payload["sub"])
    if user is None or not user.mfa_enabled or not user.mfa_secret:
        raise UnauthorizedError("MFA is not available for this account")

    if not await _verify_code_or_backup(db, user, code):
        raise UnauthorizedError("Incorrect verification code")

    memberships = await _membership_summaries(db, user.id)
    primary = next((m for m in memberships if m.is_primary), memberships[0] if memberships else None)
    await repository.touch_last_login(db, user.id)
    session, _ = await _issue_tokens_for_membership(
        db,
        user=user,
        membership_summary=primary,
        all_memberships=memberships,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    return session


async def _verify_code_or_backup(db: AsyncSession, user: User, code: str) -> bool:
    secret = decrypt_mfa_secret(user.mfa_secret)
    if verify_totp(secret, code):
        return True
    code_hash = hash_backup_code(code)
    if user.mfa_backup_codes and code_hash in user.mfa_backup_codes:
        # Single-use: a backup code that verified once is removed immediately,
        # the same discipline as the refresh-token rotation above — otherwise
        # a single leaked backup code grants unlimited future logins instead
        # of exactly one.
        remaining = [c for c in user.mfa_backup_codes if c != code_hash]
        await repository.set_mfa(db, user.id, enabled=True, secret=user.mfa_secret, backup_codes=remaining)
        user.mfa_backup_codes = remaining
        return True
    return False


async def switch_tenant(
    db: AsyncSession, *, user_id: uuid.UUID, membership_id: uuid.UUID, user_agent: str | None, ip_address: str | None
) -> SessionResponse:
    await _set_user_scope(db, user_id)
    membership = await repository.get_membership(db, membership_id)
    if membership is None or membership.user_id != user_id or membership.status != "active":
        raise NotFoundError("No such membership for this account")
    user = await repository.get_user_by_id(db, user_id)
    memberships = await _membership_summaries(db, user_id)
    target = next(m for m in memberships if m.id == membership_id)
    session, _ = await _issue_tokens_for_membership(
        db,
        user=user,
        membership_summary=target,
        all_memberships=memberships,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    return session


async def refresh_session(
    db: AsyncSession, *, raw_refresh_token: str, user_agent: str | None, ip_address: str | None
) -> SessionResponse:
    token_hash = hash_refresh_token(raw_refresh_token)
    row = await repository.get_refresh_token_by_hash(db, token_hash)
    if row is None:
        raise UnauthorizedError("Invalid refresh token")
    if row.revoked_at is not None:
        # Reuse of an already-rotated token: treat as theft, burn the chain.
        # Committed immediately, before raising — the caller's request-scoped
        # session normally rolls back on an exception, which would otherwise
        # silently undo this exact revocation and leave the stolen chain live.
        await repository.revoke_refresh_token_family(db, row.family_id)
        await db.commit()
        raise UnauthorizedError("Refresh token reuse detected — all sessions for this device chain were revoked")
    if row.expires_at < datetime.now(UTC):
        raise UnauthorizedError("Refresh token has expired")

    user = await repository.get_user_by_id(db, row.user_id)
    if user is None or user.status != "active":
        raise UnauthorizedError("Account is not active")

    memberships = await _membership_summaries(db, user.id)
    target = None
    if row.membership_id:
        target = next((m for m in memberships if m.id == row.membership_id), None)
    if target is None:
        target = next((m for m in memberships if m.is_primary), memberships[0] if memberships else None)

    response, new_row = await _issue_tokens_for_membership(
        db,
        user=user,
        membership_summary=target,
        all_memberships=memberships,
        user_agent=user_agent,
        ip_address=ip_address,
        reuse_family_id=row.family_id,
    )
    await repository.revoke_refresh_token(db, row.id, replaced_by_id=new_row.id)
    return response


async def revoke_refresh_token(db: AsyncSession, raw_refresh_token: str) -> None:
    row = await repository.get_refresh_token_by_hash(db, hash_refresh_token(raw_refresh_token))
    if row is not None and row.revoked_at is None:
        await repository.revoke_refresh_token(db, row.id)


async def start_mfa_setup(db: AsyncSession, user: User) -> MfaSetupResponse:
    secret = generate_totp_secret()
    backup_codes = generate_backup_codes()
    await repository.set_mfa(
        db,
        user.id,
        enabled=False,  # not enabled until verify_mfa_setup succeeds
        secret=encrypt_mfa_secret(secret),
        backup_codes=[hash_backup_code(c) for c in backup_codes],
    )
    return MfaSetupResponse(
        secret=secret,
        provisioning_uri=totp_provisioning_uri(secret, user.email),
        backup_codes=backup_codes,
    )


async def verify_mfa_setup(db: AsyncSession, user: User, code: str) -> None:
    if not user.mfa_secret:
        raise ForbiddenError("No MFA setup is in progress for this account")
    secret = decrypt_mfa_secret(user.mfa_secret)
    if not verify_totp(secret, code):
        raise UnauthorizedError("Incorrect verification code")
    await repository.set_mfa(db, user.id, enabled=True, secret=user.mfa_secret, backup_codes=user.mfa_backup_codes)


async def disable_mfa(db: AsyncSession, user: User) -> None:
    await repository.set_mfa(db, user.id, enabled=False, secret=None, backup_codes=None)


async def issue_step_up_token(db: AsyncSession, user: User, code: str, tenant_id: uuid.UUID | None) -> StepUpResponse:
    if not user.mfa_enabled or not user.mfa_secret:
        raise ForbiddenError("Step-up re-authentication requires MFA to be enabled on this account")
    if not await _verify_code_or_backup(db, user, code):
        raise UnauthorizedError("Incorrect verification code")
    settings = get_settings()
    token = create_step_up_token(user_id=user.id, tenant_id=tenant_id)
    return StepUpResponse(step_up_token=token, expires_in_seconds=settings.step_up_token_ttl_minutes * 60)
