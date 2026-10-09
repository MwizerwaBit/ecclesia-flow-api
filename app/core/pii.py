"""Identity-document numbers (national ID, passport) — never stored in clear.

Three derived values are kept instead:
  hash       HMAC-SHA256 under a server key, over the normalised number. Used
             for matching ("is this person already registered?") and for the
             one-person-per-ID uniqueness rule. A keyed hash, not a plain one:
             ID numbers are short and structured, so an unkeyed hash could be
             brute-forced straight back to the number.
  encrypted  Fernet ciphertext, for the rare authorised need to see it again.
  last4      What every screen shows.

The key comes from PII_ENCRYPTION_KEY, separate from the JWT and MFA keys so
rotating one never touches the others.
"""

import base64
import hashlib
import hmac
import re

from cryptography.fernet import Fernet

from app.core.config import get_settings

ID_TYPES = ("national_id", "passport", "other")


def normalise(id_type: str, value: str) -> str:
    """Case, spaces, dots and dashes don't make two numbers different."""
    cleaned = re.sub(r"[\s.\-/]", "", value).upper()
    return f"{id_type}:{cleaned}"


def _key() -> bytes:
    return get_settings().pii_encryption_key.encode()


def id_hash(id_type: str, value: str) -> str:
    return hmac.new(_key(), normalise(id_type, value).encode(), hashlib.sha256).hexdigest()


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"pii-encryption:" + _key()).digest()))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.strip().encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def last4(value: str) -> str:
    cleaned = re.sub(r"[\s.\-/]", "", value)
    return cleaned[-4:]


def protect(id_type: str, value: str) -> dict:
    """The column values to store for an ID number."""
    return {
        "id_type": id_type,
        "national_id_hash": id_hash(id_type, value),
        "national_id_encrypted": encrypt(value),
        "national_id_last4": last4(value),
    }
