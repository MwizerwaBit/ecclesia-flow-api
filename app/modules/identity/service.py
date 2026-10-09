import uuid
from datetime import UTC, datetime, timedelta

from prisma import Prisma
from prisma.models import User

from app.core.config import get_settings
from app.core.database import clear_tenant_context, set_tenant_context, tenant_client
from app.core.exceptions import AccountLockedError, ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
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
from app.modules.identity.schemas import (
    MembershipSummary,
    MfaChallengeResponse,
    MfaSetupResponse,
    RegisterRequest,
    SessionResponse,
    StepUpResponse,
    UserPublic,
)
from app.modules.rbac.models import SYSTEM_ROLE_BOARD_ID, SYSTEM_ROLE_LEADER_ID, SYSTEM_ROLE_NAMES
from app.modules.rbac.service import resolve_permissions
from app.modules.tenant.service import create_organization_with_root_unit


async def _set_user_scope(db: Prisma, user_id: str) -> None:
    await db.execute_raw("select set_config('app.user_id', $1, true)", user_id)


async def _membership_summaries(db: Prisma, user_id: str) -> list[MembershipSummary]:
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
    db: Prisma,
    *,
    user: User,
    membership_summary: MembershipSummary | None,
    all_memberships: list[MembershipSummary],
    user_agent: str | None,
    ip_address: str | None,
    mfa_verified: bool,
    reuse_family_id: str | None = None,
):
    """Mints a fresh access token AND a fresh refresh token.

    ``reuse_family_id`` is set only by the refresh-rotation path: the new
    refresh token stays part of the SAME rotation chain as the one it
    replaces, so a reuse of any earlier token in the chain still burns the
    whole family, not just the newest link."""
    permissions: list[str] = []
    role_id: str | None = None
    if membership_summary is not None:
        membership = await repository.get_membership(db, str(membership_summary.id))
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
        # True only when this session actually passed a TOTP/backup-code
        # check; MFA-gated permissions rely on it (app.core.permissions).
        mfa_verified=mfa_verified,
    )

    settings = get_settings()
    raw_refresh = new_refresh_token()
    new_token_row = await repository.store_refresh_token(
        db,
        user_id=user.id,
        membership_id=str(membership_summary.id) if membership_summary else None,
        token_hash=hash_refresh_token(raw_refresh),
        family_id=reuse_family_id or str(uuid.uuid4()),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
        user_agent=user_agent,
        ip_address=ip_address,
        mfa_verified=mfa_verified,
    )

    return (
        SessionResponse(
            access_token=access_token,
            refresh_token=raw_refresh,
            user=UserPublic.model_validate(user),
            active_membership=membership_summary,
            memberships=all_memberships,
            permissions=permissions,
            role=SYSTEM_ROLE_NAMES.get(role_id) if role_id else None,
            unit_scope_id=membership_summary.unit_scope_id if membership_summary else None,
        ),
        new_token_row,
    )


async def register_church_leader(
    db: Prisma, payload: RegisterRequest, *, user_agent: str | None, ip_address: str | None
) -> SessionResponse:
    """Self-serve church registration — one transaction: organisation + root
    unit + the registrant's account and membership + an invitation for the
    other person in charge. The church starts *pending*: it can finish
    onboarding straight away, and becomes active when it pays (or when
    EcclesiaFlow activates it)."""
    from app.modules.team import service as team_service

    existing = await repository.get_user_by_email(db, payload.email)
    if existing is not None:
        raise ForbiddenError("An account with this email already exists")

    org, _root_unit_id = await create_organization_with_root_unit(
        db,
        legal_name=payload.legal_name or payload.church_name,
        display_name=payload.church_name,
        country=payload.country,
        currency=payload.currency,
        timezone_name=payload.timezone,
    )
    settings = get_settings()
    await db.organization.update(
        where={"id": org.id},
        data={
            "status": "active" if settings.dev_auto_activate_orgs else "pending",
            "denomination": payload.denomination,
            "registration_number": payload.registration_number,
            "contact_email": payload.email.lower(),
            "contact_phone": payload.contact_phone,
            "address_line1": payload.address_line1,
            "city": payload.city,
            "region": payload.region,
        },
    )

    user = await repository.create_user(
        db,
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        first_name=payload.first_name,
        last_name=payload.last_name,
    )

    is_leader = payload.registrant_role == "leader"
    role_id = SYSTEM_ROLE_LEADER_ID if is_leader else SYSTEM_ROLE_BOARD_ID
    membership = await repository.create_membership(
        db,
        user_id=user.id,
        tenant_id=org.id,
        role_id=role_id,
        is_primary=True,
        is_leader=is_leader,
    )

    invite_url = None
    if payload.other_person is not None:
        invited = await team_service.invite_with_role(
            db,
            tenant_id=org.id,
            email=payload.other_person.email,
            first_name=payload.other_person.first_name,
            last_name=payload.other_person.last_name,
            # The administrator invites the leader; the leader invites an administrator.
            role_id=SYSTEM_ROLE_BOARD_ID if is_leader else SYSTEM_ROLE_LEADER_ID,
            unit_scope_id=None,
            is_leader=not is_leader,
        )
        invite_url = invited.get("invite_url")

    await write_audit(
        db,
        tenant_id=org.id,
        actor_user_id=user.id,
        action="organization.registered",
        resource_type="organization",
        resource_id=org.id,
        metadata={"registrant_role": payload.registrant_role},
        ip_address=ip_address,
    )

    summary = MembershipSummary(
        id=membership.id,
        tenant_id=org.id,
        tenant_name=org.display_name,
        role_name=SYSTEM_ROLE_NAMES[role_id],
        is_primary=True,
        is_leader=is_leader,
        unit_scope_id=None,
    )
    session, _ = await _issue_tokens_for_membership(
        db,
        user=user,
        membership_summary=summary,
        all_memberships=[summary],
        user_agent=user_agent,
        ip_address=ip_address,
        mfa_verified=False,
    )
    session.invite_url = invite_url
    return session


async def join_church(db: Prisma, payload, *, user_agent: str | None, ip_address: str | None) -> SessionResponse:
    """Self-registration into a church, linked to their existing record when
    there is one.

    Linking is automatic only when the ID number AND the name both match a
    record that isn't already someone's login — an ID number alone is not a
    secret, so a match on it alone never hands over someone's record. Any
    mismatch gets one neutral message pointing to the church office."""
    from app.core import pii
    from app.modules.people import repository as people_repository
    from app.modules.rbac.models import SYSTEM_ROLE_MEMBER_ID

    org = await db.organization.find_unique(where={"slug": payload.church_slug})
    if org is None or org.status not in ("active", "trial"):
        raise NotFoundError("No church found at this address")
    if await repository.get_user_by_email(db, payload.email.lower()) is not None:
        raise ForbiddenError("An account with this email already exists — sign in instead")

    await set_tenant_context(db, org.id)
    protected = pii.protect(payload.id_type, payload.national_id)
    match = await people_repository.find_by_id_hash(db, protected["national_id_hash"])
    if match is not None:
        same_person = (
            match["first_name"].strip().lower() == payload.first_name.strip().lower()
            and match["last_name"].strip().lower() == payload.last_name.strip().lower()
        )
        if not same_person or match["user_id"] is not None:
            raise ConflictError(
                "We couldn't match these details to the church's records. Please contact the church office.",
                code="identity_mismatch",
            )

    user = await repository.create_user(
        db,
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
    )
    consent = {
        "consent_data_processing_at": datetime.now(UTC),
        "consent_given_by": "self",
        "consent_communications": payload.consent_communications,
    }
    if match is not None:
        member_id = match["id"]
        existing = await db.member.find_unique(where={"id": member_id})
        await db.member.update(
            where={"id": member_id},
            data={
                "user_id": user.id,
                # Fill gaps only; never overwrite what the church recorded.
                **({"email": payload.email.lower()} if not existing.email else {}),
                **({"phone": payload.phone} if not existing.phone else {}),
                **({} if existing.consent_data_processing_at else consent),
            },
        )
        action = "member.self_linked"
    else:
        member = await db.member.create(
            data={
                "tenant_id": org.id,
                "user_id": user.id,
                "first_name": payload.first_name.strip(),
                "last_name": payload.last_name.strip(),
                "email": payload.email.lower(),
                "phone": payload.phone,
                "gender": payload.gender,
                "date_of_birth": datetime.combine(payload.date_of_birth, datetime.min.time(), tzinfo=UTC),
                "status": "visitor",
                "join_method": "first_visit",
                **protected,
                **consent,
            }
        )
        member_id = member.id
        action = "member.self_registered"

    membership = await repository.create_membership(
        db, user_id=user.id, tenant_id=org.id, role_id=SYSTEM_ROLE_MEMBER_ID, is_primary=True
    )
    await write_audit(
        db,
        tenant_id=org.id,
        actor_user_id=user.id,
        action=action,
        resource_type="member",
        resource_id=member_id,
        ip_address=ip_address,
    )
    # app.tenant_id stays set (transaction-local) so the new membership is
    # visible while its first tokens are issued.
    summary = MembershipSummary(
        id=membership.id,
        tenant_id=org.id,
        tenant_name=org.display_name,
        role_name="member",
        is_primary=True,
        is_leader=False,
        unit_scope_id=None,
    )
    session, _ = await _issue_tokens_for_membership(
        db,
        user=user,
        membership_summary=summary,
        all_memberships=[summary],
        user_agent=user_agent,
        ip_address=ip_address,
        mfa_verified=False,
    )
    return session


async def _fail_login(user: User | None) -> None:
    """Records a failure in its OWN transaction: the request's transaction
    rolls back when we raise, which would otherwise erase the count and make
    the lockout unreachable (same reasoning as refresh-reuse revocation)."""
    if user is None:
        return
    settings = get_settings()
    async with tenant_client.tx() as tx:
        await repository.record_failed_login(
            tx, user.id, max_failures=settings.max_failed_logins, lockout_minutes=settings.lockout_minutes
        )


def _locked(user: User) -> bool:
    return user.locked_until is not None and user.locked_until > datetime.now(UTC)


async def authenticate(
    db: Prisma, *, email: str, password: str, user_agent: str | None, ip_address: str | None
) -> SessionResponse | MfaChallengeResponse:
    user = await repository.get_user_by_email(db, email.lower())
    password_ok = user is not None and user.password_hash is not None and verify_password(password, user.password_hash)

    if user is not None and _locked(user):
        # Only someone who already knows the password learns the account is
        # locked; a guesser keeps getting the generic error below.
        if password_ok:
            raise AccountLockedError(
                "Too many failed sign-in attempts. Try again later.", locked_until=user.locked_until
            )
        raise UnauthorizedError("Incorrect email or password")

    # Constant-shape failure: a wrong password and a nonexistent email return
    # the identical error, so the endpoint can't be used to enumerate accounts.
    if not password_ok:
        await _fail_login(user)
        raise UnauthorizedError("Incorrect email or password")
    if user.status != "active":
        raise ForbiddenError("This account is not active")

    if user.mfa_enabled:
        # The failure counter resets only once the second factor also passes.
        return MfaChallengeResponse(challenge_token=create_mfa_challenge_token(user_id=user.id))

    await repository.clear_failed_logins(db, user.id)
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
        mfa_verified=False,
    )
    return session


async def complete_mfa_challenge(
    db: Prisma, *, challenge_token: str, code: str, user_agent: str | None, ip_address: str | None
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
    if _locked(user):
        raise AccountLockedError("Too many failed sign-in attempts. Try again later.", locked_until=user.locked_until)

    if not await _verify_code_or_backup(db, user, code):
        # Wrong second-factor codes count toward the same lockout, so a stolen
        # password can't be paired with unlimited TOTP guesses.
        await _fail_login(user)
        raise UnauthorizedError("Incorrect verification code")

    await repository.clear_failed_logins(db, user.id)
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
        mfa_verified=True,
    )
    return session


async def _verify_code_or_backup(db: Prisma, user: User, code: str) -> bool:
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
    db: Prisma,
    *,
    user_id: str,
    membership_id: str,
    mfa_verified: bool,
    user_agent: str | None,
    ip_address: str | None,
) -> SessionResponse:
    await _set_user_scope(db, user_id)
    membership = await repository.get_membership(db, membership_id)
    if membership is None or membership.user_id != user_id or membership.status != "active":
        raise NotFoundError("No such membership for this account")
    user = await repository.get_user_by_id(db, user_id)
    memberships = await _membership_summaries(db, user_id)
    target = next(m for m in memberships if str(m.id) == membership_id)
    session, _ = await _issue_tokens_for_membership(
        db,
        user=user,
        membership_summary=target,
        all_memberships=memberships,
        user_agent=user_agent,
        ip_address=ip_address,
        mfa_verified=mfa_verified,
    )
    return session


async def refresh_session(
    db: Prisma, *, raw_refresh_token: str, user_agent: str | None, ip_address: str | None
) -> SessionResponse:
    token_hash = hash_refresh_token(raw_refresh_token)
    row = await repository.get_refresh_token_by_hash(db, token_hash)
    if row is None:
        raise UnauthorizedError("Invalid refresh token")
    if row.revoked_at is not None:
        # Reuse of an already-rotated token: treat as theft, burn the chain.
        # Run in its OWN transaction, independent of the caller's — the
        # caller's request-scoped transaction rolls back when this function
        # raises below, which would otherwise silently undo this exact
        # revocation and leave the stolen chain live (see
        # docs/SECURITY_NOTES.md §2 — Prisma has no mid-transaction manual
        # commit, so "commit before raising" has to mean a separate
        # transaction, not a call on this one).
        async with tenant_client.tx() as revoke_tx:
            await repository.revoke_refresh_token_family(revoke_tx, row.family_id)
        raise UnauthorizedError("Refresh token reuse detected — all sessions for this device chain were revoked")
    if row.expires_at < datetime.now(UTC):
        raise UnauthorizedError("Refresh token has expired")

    user = await repository.get_user_by_id(db, row.user_id)
    if user is None or user.status != "active":
        raise UnauthorizedError("Account is not active")
    if user.password_changed_at and row.created_at < user.password_changed_at:
        # Sessions started before a password change die with the old password.
        raise UnauthorizedError("Your password was changed — sign in again", code="session_revoked")

    memberships = await _membership_summaries(db, user.id)
    target = None
    if row.membership_id:
        target = next((m for m in memberships if str(m.id) == row.membership_id), None)
    if target is None:
        target = next((m for m in memberships if m.is_primary), memberships[0] if memberships else None)

    response, new_row = await _issue_tokens_for_membership(
        db,
        user=user,
        membership_summary=target,
        all_memberships=memberships,
        user_agent=user_agent,
        ip_address=ip_address,
        mfa_verified=row.mfa_verified,
        reuse_family_id=row.family_id,
    )
    await repository.revoke_refresh_token(db, row.id, replaced_by_id=new_row.id)
    return response


async def revoke_refresh_token(db: Prisma, raw_refresh_token: str) -> None:
    """Sign-out ends the whole session (every rotation of this device's
    refresh chain), not just the one token presented."""
    row = await repository.get_refresh_token_by_hash(db, hash_refresh_token(raw_refresh_token))
    if row is not None:
        await repository.revoke_refresh_token_family(db, row.family_id)


async def accept_invite(
    db: Prisma,
    *,
    raw_token: str,
    password: str,
    first_name: str,
    last_name: str,
    user_agent: str | None,
    ip_address: str | None,
) -> SessionResponse:
    """Turns an invitation into a working sign-in.

    The token is "<tenant_id>.<secret>": the tenant part only selects which
    church's rows are visible (RLS); the SHA-256 of the whole token must match
    the stored hash, so a guessed or altered tenant id finds nothing. A
    brand-new invitee chooses their password here; someone who already has an
    EcclesiaFlow account proves it with their existing password instead."""
    tenant_part, _, secret = raw_token.partition(".")
    try:
        uuid.UUID(tenant_part)
    except ValueError as exc:
        raise UnauthorizedError("This invitation link is not valid") from exc
    if not secret:
        raise UnauthorizedError("This invitation link is not valid")

    await set_tenant_context(db, tenant_part)
    membership = await repository.find_membership_by_invite_hash(db, hash_refresh_token(raw_token))
    if membership is None or membership.status != "invited":
        raise UnauthorizedError("This invitation link is not valid")
    if membership.invite_expires_at is None or membership.invite_expires_at < datetime.now(UTC):
        raise UnauthorizedError("This invitation has expired — ask for a new one", code="invite_expired")

    user = await repository.get_user_by_id(db, membership.user_id)
    if user is None or user.status != "active":
        raise UnauthorizedError("This invitation link is not valid")
    if user.password_hash:
        if not verify_password(password, user.password_hash):
            await _fail_login(user)
            raise UnauthorizedError("Incorrect password for your existing account")
    else:
        await repository.set_password(
            db, user.id, password_hash=hash_password(password), first_name=first_name, last_name=last_name
        )
    await repository.accept_membership_invite(db, membership.id)
    await write_audit(
        db,
        tenant_id=membership.tenant_id,
        actor_user_id=user.id,
        action="team.invite_accepted",
        resource_type="tenant_membership",
        resource_id=membership.id,
        ip_address=ip_address,
    )
    await clear_tenant_context(db)

    user = await repository.get_user_by_id(db, user.id)
    memberships = await _membership_summaries(db, user.id)
    target = next((m for m in memberships if str(m.id) == membership.id), None)
    session, _ = await _issue_tokens_for_membership(
        db,
        user=user,
        membership_summary=target,
        all_memberships=memberships,
        user_agent=user_agent,
        ip_address=ip_address,
        mfa_verified=False,
    )
    return session


async def start_mfa_setup(db: Prisma, user: User) -> MfaSetupResponse:
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


async def verify_mfa_setup(db: Prisma, user: User, code: str) -> None:
    if not user.mfa_secret:
        raise ForbiddenError("No MFA setup is in progress for this account")
    secret = decrypt_mfa_secret(user.mfa_secret)
    if not verify_totp(secret, code):
        raise UnauthorizedError("Incorrect verification code")
    await repository.set_mfa(db, user.id, enabled=True, secret=user.mfa_secret, backup_codes=user.mfa_backup_codes)


async def disable_mfa(db: Prisma, user: User) -> None:
    await repository.set_mfa(db, user.id, enabled=False, secret=None, backup_codes=None)


async def issue_step_up_token(db: Prisma, user: User, code: str, tenant_id: str | None) -> StepUpResponse:
    if not user.mfa_enabled or not user.mfa_secret:
        raise ForbiddenError("Step-up re-authentication requires MFA to be enabled on this account")
    if not await _verify_code_or_backup(db, user, code):
        raise UnauthorizedError("Incorrect verification code")
    settings = get_settings()
    token = create_step_up_token(user_id=user.id, tenant_id=tenant_id)
    return StepUpResponse(step_up_token=token, expires_in_seconds=settings.step_up_token_ttl_minutes * 60)
