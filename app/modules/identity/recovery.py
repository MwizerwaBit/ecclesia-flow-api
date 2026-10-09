"""Account recovery — "forgot password" (DIF-07).

Guarantees, each covered by tests/test_engineering.py:

- **No account enumeration.** A reset request gets the same response, and
  does the same database work, whether or not the email has an account. The
  email itself is sent after the response, so its delivery time can't be
  measured either.
- **Tokens:** 256 bits from ``secrets``, stored only as SHA-256, valid for
  ``PASSWORD_RESET_TTL_MINUTES``, single use (consumed with a conditional
  update, so two simultaneous uses can't both win). Requesting a new link
  voids older unused ones. At most ``PASSWORD_RESET_MAX_PER_HOUR`` emails per
  account per hour, on top of the per-IP rate limit.
- **After a reset:** every refresh token for the account is revoked, access
  tokens issued before the change stop working on their next request
  (``deps._ensure_session_current``), the lockout counter is cleared, and the
  change is written to the account-level audit chain.
"""

import secrets
from datetime import UTC, datetime, timedelta

from prisma import Prisma

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError
from app.core.mailer import OutgoingEmail
from app.core.security import hash_password, hash_refresh_token
from app.modules.audit.service import write_audit

_INVALID = "This reset link is invalid or has expired. Ask for a new one."


def _now() -> datetime:
    return datetime.now(UTC)


async def request_reset(db: Prisma, *, email: str, ip_address: str | None) -> OutgoingEmail | None:
    """Returns the email to send after the response, or None. Callers must
    respond identically either way."""
    settings = get_settings()
    raw_token = secrets.token_urlsafe(32)  # generated regardless, so both paths do the same work
    user = await db.user.find_unique(where={"email": email.strip().lower()})
    if user is None or user.status != "active" or not user.password_hash:
        return None

    recent = await db.passwordresettoken.count(
        where={"user_id": user.id, "created_at": {"gte": _now() - timedelta(hours=1)}}
    )
    if recent >= settings.password_reset_max_per_hour:
        return None

    await db.passwordresettoken.update_many(where={"user_id": user.id, "used_at": None}, data={"used_at": _now()})
    await db.passwordresettoken.create(
        data={
            "user_id": user.id,
            "token_hash": hash_refresh_token(raw_token),
            "expires_at": _now() + timedelta(minutes=settings.password_reset_ttl_minutes),
            "requested_ip": ip_address,
        }
    )
    await write_audit(
        db,
        tenant_id=None,
        actor_user_id=None,
        action="account.password_reset_requested",
        resource_type="user",
        resource_id=user.id,
        ip_address=ip_address,
    )
    link = f"{settings.frontend_url.rstrip('/')}/reset-password#{raw_token}"
    return OutgoingEmail(
        to=user.email,
        subject="Reset your EcclesiaFlow password",
        body=(
            f"Hello {user.first_name},\n\n"
            "Someone asked to reset the password for your EcclesiaFlow account. "
            f"To choose a new password, open this link within {settings.password_reset_ttl_minutes} minutes:\n\n"
            f"{link}\n\n"
            "The link works once. If you didn't ask for this, ignore this email — your password hasn't changed.\n"
        ),
    )


async def complete_reset(db: Prisma, *, raw_token: str, new_password: str, ip_address: str | None) -> None:
    row = await db.passwordresettoken.find_unique(where={"token_hash": hash_refresh_token(raw_token)})
    if row is None or row.used_at is not None or row.expires_at <= _now():
        raise UnauthorizedError(_INVALID, code="reset_invalid")
    user = await db.user.find_unique(where={"id": row.user_id})
    if user is None or user.status != "active":
        raise UnauthorizedError(_INVALID, code="reset_invalid")

    # Consume first, conditionally: of two simultaneous uses only one updates a row.
    consumed = await db.execute_raw(
        "update password_reset_tokens set used_at = now() where id = $1::uuid and used_at is null", row.id
    )
    if consumed != 1:
        raise UnauthorizedError(_INVALID, code="reset_invalid")

    await db.execute_raw(
        """
        update users set password_hash = $2, password_changed_at = now(),
               failed_login_count = 0, locked_until = null
         where id = $1::uuid
        """,
        user.id,
        hash_password(new_password),
    )
    await db.passwordresettoken.update_many(where={"user_id": user.id, "used_at": None}, data={"used_at": _now()})
    await db.refreshtoken.update_many(where={"user_id": user.id, "revoked_at": None}, data={"revoked_at": _now()})
    await write_audit(
        db,
        tenant_id=None,
        actor_user_id=user.id,
        action="account.password_reset_completed",
        resource_type="user",
        resource_id=user.id,
        ip_address=ip_address,
    )
