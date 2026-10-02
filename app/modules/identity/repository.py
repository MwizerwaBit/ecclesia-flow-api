import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import RefreshToken, TenantMembership, User


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: str | uuid.UUID) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    password_hash: str,
    first_name: str,
    last_name: str,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        password_hash=password_hash,
        first_name=first_name,
        last_name=last_name,
        created_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()
    return user


async def touch_last_login(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(update(User).where(User.id == user_id).values(last_login_at=datetime.now(UTC)))


async def set_mfa(
    db: AsyncSession, user_id: uuid.UUID, *, enabled: bool, secret: str | None, backup_codes: list[str] | None
) -> None:
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(mfa_enabled=enabled, mfa_secret=secret, mfa_backup_codes=backup_codes)
    )


async def list_memberships_for_user(db: AsyncSession, user_id: str | uuid.UUID) -> list[dict]:
    """Relies on the tenant_memberships RLS self-visibility clause — the
    caller must be inside a `self_scoped_session` (app.user_id set, no
    app.tenant_id), not an ordinary tenant_session."""
    result = await db.execute(
        text(
            """
            select tm.id, tm.tenant_id, o.display_name as tenant_name, r.name as role_name,
                   tm.is_primary, tm.is_leader, tm.unit_scope_id, tm.status
            from tenant_memberships tm
            join organizations o on o.id = tm.tenant_id
            join roles r on r.id = tm.role_id
            where tm.user_id = :user_id and tm.status = 'active'
            order by tm.is_primary desc, tm.created_at asc
            """
        ),
        {"user_id": str(user_id)},
    )
    return [dict(row._mapping) for row in result.all()]


async def get_membership(db: AsyncSession, membership_id: str | uuid.UUID) -> TenantMembership | None:
    result = await db.execute(select(TenantMembership).where(TenantMembership.id == membership_id))
    return result.scalar_one_or_none()


async def create_membership(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role_id: uuid.UUID,
    is_primary: bool = False,
    is_leader: bool = False,
    status: str = "active",
) -> TenantMembership:
    now = datetime.now(UTC)
    membership = TenantMembership(
        id=uuid.uuid4(),
        user_id=user_id,
        tenant_id=tenant_id,
        role_id=role_id,
        is_primary=is_primary,
        is_leader=is_leader,
        status=status,
        accepted_at=now if status == "active" else None,
        created_at=now,
    )
    db.add(membership)
    await db.flush()
    return membership


async def store_refresh_token(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    membership_id: uuid.UUID | None,
    token_hash: str,
    family_id: uuid.UUID,
    expires_at: datetime,
    user_agent: str | None,
    ip_address: str | None,
) -> RefreshToken:
    row = RefreshToken(
        id=uuid.uuid4(),
        user_id=user_id,
        membership_id=membership_id,
        token_hash=token_hash,
        family_id=family_id,
        expires_at=expires_at,
        user_agent=user_agent,
        ip_address=ip_address,
        created_at=datetime.now(UTC),
    )
    db.add(row)
    await db.flush()
    return row


async def get_refresh_token_by_hash(db: AsyncSession, token_hash: str) -> RefreshToken | None:
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    return result.scalar_one_or_none()


async def revoke_refresh_token(
    db: AsyncSession, token_id: uuid.UUID, *, replaced_by_id: uuid.UUID | None = None
) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == token_id)
        .values(revoked_at=datetime.now(UTC), replaced_by_id=replaced_by_id)
    )


async def revoke_refresh_token_family(db: AsyncSession, family_id: uuid.UUID) -> None:
    """A rotated token presented a second time means it was stolen — revoke
    every token ever issued in its rotation chain, not just the one reused."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
