from datetime import UTC, datetime

from prisma import Prisma
from prisma.models import PasswordResetToken, RefreshToken, TenantMembership, User


async def get_user_by_email(db: Prisma, email: str) -> User | None:
    return await db.user.find_unique(where={"email": email})


async def get_user_by_id(db: Prisma, user_id: str) -> User | None:
    return await db.user.find_unique(where={"id": user_id})


async def create_user(db: Prisma, *, email: str, password_hash: str, first_name: str, last_name: str) -> User:
    return await db.user.create(
        data={
            "email": email,
            "password_hash": password_hash,
            "first_name": first_name,
            "last_name": last_name,
        }
    )


async def touch_last_login(db: Prisma, user_id: str) -> None:
    await db.user.update(where={"id": user_id}, data={"last_login_at": datetime.now(UTC)})


async def set_mfa(
    db: Prisma, user_id: str, *, enabled: bool, secret: str | None, backup_codes: list[str] | None
) -> None:
    await db.user.update(
        where={"id": user_id},
        data={"mfa_enabled": enabled, "mfa_secret": secret, "mfa_backup_codes": backup_codes or []},
    )


async def list_memberships_for_user(db: Prisma, user_id: str) -> list[dict]:
    """Relies on the tenant_memberships RLS self-visibility clause — the
    caller must be inside a `self_scoped_session` (app.user_id set, no
    app.tenant_id), not an ordinary tenant_session."""
    rows = await db.query_raw(
        """
        select tm.id, tm.tenant_id, o.display_name as tenant_name, r.name as role_name,
               tm.is_primary, tm.is_leader, tm.unit_scope_id, tm.status
        from tenant_memberships tm
        join organizations o on o.id = tm.tenant_id
        join roles r on r.id = tm.role_id
        where tm.user_id = $1::uuid and tm.status = 'active'
        order by tm.is_primary desc, tm.created_at asc
        """,
        user_id,
    )
    return rows


async def church_idle_minutes(db: Prisma, tenant_id: str) -> int | None:
    rows = await db.query_raw("select session_idle_minutes from organizations where id = $1::uuid", tenant_id)
    return rows[0]["session_idle_minutes"] if rows else None


async def get_membership(db: Prisma, membership_id: str) -> TenantMembership | None:
    return await db.tenantmembership.find_unique(where={"id": membership_id})


async def create_membership(
    db: Prisma,
    *,
    user_id: str,
    tenant_id: str,
    role_id: str,
    is_primary: bool = False,
    is_leader: bool = False,
    status: str = "active",
) -> TenantMembership:
    now = datetime.now(UTC)
    return await db.tenantmembership.create(
        data={
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role_id": role_id,
            "is_primary": is_primary,
            "is_leader": is_leader,
            "status": status,
            "accepted_at": now if status == "active" else None,
        }
    )


async def store_refresh_token(
    db: Prisma,
    *,
    user_id: str,
    membership_id: str | None,
    token_hash: str,
    family_id: str,
    expires_at: datetime,
    user_agent: str | None,
    ip_address: str | None,
    mfa_verified: bool = False,
) -> RefreshToken:
    return await db.refreshtoken.create(
        data={
            "mfa_verified": mfa_verified,
            "user_id": user_id,
            "membership_id": membership_id,
            "token_hash": token_hash,
            "family_id": family_id,
            "expires_at": expires_at,
            "user_agent": user_agent,
            "ip_address": ip_address,
        }
    )


async def get_refresh_token_by_hash(db: Prisma, token_hash: str) -> RefreshToken | None:
    return await db.refreshtoken.find_unique(where={"token_hash": token_hash})


async def revoke_refresh_token(db: Prisma, token_id: str, *, replaced_by_id: str | None = None) -> None:
    await db.refreshtoken.update(
        where={"id": token_id},
        data={"revoked_at": datetime.now(UTC), "replaced_by_id": replaced_by_id},
    )


async def revoke_refresh_token_family(db: Prisma, family_id: str) -> None:
    """A rotated token presented a second time means it was stolen — revoke
    every token ever issued in its rotation chain, not just the one reused."""
    await db.refreshtoken.update_many(
        where={"family_id": family_id, "revoked_at": None},
        data={"revoked_at": datetime.now(UTC)},
    )


# ───────────────────────────── Lockout ─────────────────────────────


async def record_failed_login(db: Prisma, user_id: str, *, max_failures: int, lockout_minutes: int) -> bool:
    """Counts one failure; locks the account once the threshold is reached.
    Returns True when this failure triggered the lock."""
    rows = await db.query_raw(
        """
        update users set
          failed_login_count = failed_login_count + 1,
          last_failed_login_at = now(),
          locked_until = case when failed_login_count + 1 >= $2::int
                              then now() + make_interval(mins => $3::int) else locked_until end
        where id = $1::uuid
        returning failed_login_count
        """,
        user_id,
        max_failures,
        lockout_minutes,
    )
    if rows and rows[0]["failed_login_count"] >= max_failures:
        await db.execute_raw("update users set failed_login_count = 0 where id = $1::uuid", user_id)
        return True
    return False


async def clear_failed_logins(db: Prisma, user_id: str) -> None:
    await db.execute_raw("update users set failed_login_count = 0, locked_until = null where id = $1::uuid", user_id)


# ───────────────────────────── Invitations ─────────────────────────────


async def find_membership_by_invite_hash(db: Prisma, token_hash: str) -> TenantMembership | None:
    return await db.tenantmembership.find_unique(where={"invite_token_hash": token_hash})


async def accept_membership_invite(db: Prisma, membership_id: str) -> None:
    await db.tenantmembership.update(
        where={"id": membership_id},
        data={
            "status": "active",
            "accepted_at": datetime.now(UTC),
            "invite_token_hash": None,
            "invite_expires_at": None,
        },
    )


# ───────────────────────────── Password reset ─────────────────────────────


async def count_recent_reset_tokens(db: Prisma, user_id: str, *, since: datetime) -> int:
    return await db.passwordresettoken.count(where={"user_id": user_id, "created_at": {"gte": since}})


async def void_unused_reset_tokens(db: Prisma, user_id: str) -> None:
    await db.passwordresettoken.update_many(
        where={"user_id": user_id, "used_at": None}, data={"used_at": datetime.now(UTC)}
    )


async def create_reset_token(
    db: Prisma, *, user_id: str, token_hash: str, expires_at: datetime, requested_ip: str | None
) -> PasswordResetToken:
    return await db.passwordresettoken.create(
        data={"user_id": user_id, "token_hash": token_hash, "expires_at": expires_at, "requested_ip": requested_ip}
    )


async def get_reset_token_by_hash(db: Prisma, token_hash: str) -> PasswordResetToken | None:
    return await db.passwordresettoken.find_unique(where={"token_hash": token_hash})


async def consume_reset_token(db: Prisma, token_id: str) -> int:
    """Conditional update: of two simultaneous uses only one updates a row."""
    return await db.execute_raw(
        "update password_reset_tokens set used_at = now() where id = $1::uuid and used_at is null", token_id
    )


async def reset_password_and_clear_lockout(db: Prisma, user_id: str, *, password_hash: str) -> None:
    await db.execute_raw(
        """
        update users set password_hash = $2, password_changed_at = now(),
               failed_login_count = 0, locked_until = null
         where id = $1::uuid
        """,
        user_id,
        password_hash,
    )


async def revoke_all_refresh_tokens(db: Prisma, user_id: str) -> None:
    await db.refreshtoken.update_many(
        where={"user_id": user_id, "revoked_at": None}, data={"revoked_at": datetime.now(UTC)}
    )


async def set_password(db: Prisma, user_id: str, *, password_hash: str, first_name: str, last_name: str) -> None:
    # password_changed_at uses the database clock (transaction time), the same
    # clock that stamps refresh_tokens.created_at — so a session issued in the
    # same transaction is never mistaken for one from before the change.
    await db.execute_raw(
        """
        update users set password_hash = $2, first_name = coalesce(nullif($3, ''), first_name),
               last_name = coalesce(nullif($4, ''), last_name), password_changed_at = now()
        where id = $1::uuid
        """,
        user_id,
        password_hash,
        first_name,
        last_name,
    )


# ───────────────────────────── MFA challenge attempts ─────────────────────────────


async def claim_mfa_attempt(db: Prisma, *, jti: str, user_id: str, expires_at: datetime) -> int | None:
    """One row per challenge token's jti, created on its first use and
    incremented on every retry — atomically, so two concurrent requests for
    the same token can't both squeeze through. Returns the attempt count
    this call bumped it to, or None if the token was already consumed (by a
    success or by a previous call that burned it for too many attempts) —
    the caller can't tell those two apart from this alone, which is fine:
    either way the token is dead."""
    rows = await db.query_raw(
        """
        insert into mfa_challenge_attempts (id, jti, user_id, attempt_count, expires_at)
        values (gen_random_uuid(), $1, $2::uuid, 1, $3::timestamptz)
        on conflict (jti) do update
          set attempt_count = mfa_challenge_attempts.attempt_count + 1
          where mfa_challenge_attempts.consumed_at is null
        returning attempt_count
        """,
        jti,
        user_id,
        expires_at,
    )
    return rows[0]["attempt_count"] if rows else None


async def burn_mfa_attempt(db: Prisma, jti: str) -> None:
    """Consumes the token for good — on success, or once claim_mfa_attempt
    reports too many tries, so it can't be retried even within its window."""
    await db.execute_raw(
        "update mfa_challenge_attempts set consumed_at = now() where jti = $1 and consumed_at is null", jti
    )
