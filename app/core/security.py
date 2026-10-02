"""Password hashing, JWT issuance/verification, MFA secret encryption, TOTP.

Every function here is pure (no DB access) so it's trivially unit-testable
and so the one place a timing-safe comparison or a KDF parameter matters is
never duplicated.
"""
import base64
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel

from app.core.config import get_settings

_hasher = PasswordHasher()


# ───────────────────────────── Passwords ─────────────────────────────

def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, raw)
    except VerifyMismatchError:
        return False
    except Exception:
        # Malformed/legacy hash — fail closed, never raise past the auth boundary.
        return False


def needs_rehash(hashed: str) -> bool:
    return _hasher.check_needs_rehash(hashed)


# ───────────────────────────── Refresh-token hashing ─────────────────────────────
# Refresh tokens are opaque random strings; only their SHA-256 hash is stored,
# so a leaked database never yields a usable token (unlike storing it plain
# or even with a fast reversible hash).

def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ───────────────────────────── JWT access tokens ─────────────────────────────

class TokenType(StrEnum):
    ACCESS = "access"
    STEP_UP = "step_up"
    MFA_CHALLENGE = "mfa_challenge"


class AccessTokenClaims(BaseModel):
    sub: str  # user id
    membership_id: str | None = None
    tenant_id: str | None = None
    role_id: str | None = None
    permissions: list[str] = []
    unit_scope_id: str | None = None
    is_platform_admin: bool = False
    platform_admin_level: str | None = None
    mfa_verified: bool = False
    token_type: str = TokenType.ACCESS.value
    jti: str
    exp: int
    iat: int


def _encode(payload: dict[str, Any]) -> str:
    settings = get_settings()
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(
    *,
    user_id: uuid.UUID,
    membership_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None,
    role_id: uuid.UUID | None,
    permissions: list[str],
    unit_scope_id: uuid.UUID | None,
    is_platform_admin: bool = False,
    platform_admin_level: str | None = None,
    mfa_verified: bool = False,
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "membership_id": str(membership_id) if membership_id else None,
        "tenant_id": str(tenant_id) if tenant_id else None,
        "role_id": str(role_id) if role_id else None,
        "permissions": permissions,
        "unit_scope_id": str(unit_scope_id) if unit_scope_id else None,
        "is_platform_admin": is_platform_admin,
        "platform_admin_level": platform_admin_level,
        "mfa_verified": mfa_verified,
        "token_type": TokenType.ACCESS.value,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return _encode(payload)


def create_step_up_token(*, user_id: uuid.UUID, tenant_id: uuid.UUID | None) -> str:
    """A short-lived, narrowly-scoped token proving MFA was just re-verified.

    Never carries permissions/role — it's presented ALONGSIDE the normal
    access token for one sensitive call (e.g. initiating a leadership
    transfer), not used in place of it.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=settings.step_up_token_ttl_minutes)
    payload = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id) if tenant_id else None,
        "token_type": TokenType.STEP_UP.value,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return _encode(payload)


def create_mfa_challenge_token(*, user_id: uuid.UUID) -> str:
    """Issued instead of an access token when login succeeds but MFA hasn't
    been verified yet — carries no permissions/tenant context, only proves
    "this is the account whose password was just correctly entered."""
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=5)
    payload = {
        "sub": str(user_id),
        "token_type": TokenType.MFA_CHALLENGE.value,
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return _encode(payload)


class TokenError(Exception):
    pass


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Token is invalid") from exc


# ───────────────────────────── MFA (TOTP) ─────────────────────────────

def _fernet() -> Fernet:
    settings = get_settings()
    key = hashlib.sha256(settings.mfa_encryption_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def encrypt_mfa_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_mfa_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise TokenError("MFA secret could not be decrypted") from exc


def totp_provisioning_uri(secret: str, email: str, issuer: str = "EcclesiaFlow") -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verify_totp(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def generate_backup_codes(count: int = 10) -> list[str]:
    return [secrets.token_hex(5) for _ in range(count)]


def hash_backup_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()
