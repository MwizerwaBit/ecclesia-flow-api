"""Webhook signatures in Stripe's scheme, implemented without the Stripe SDK.

Header:  Stripe-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256>
Signed:  f"{t}.{raw request body}" with the endpoint's webhook secret.

Verification rejects a missing/garbled header, a bad signature (constant-time
compare), and a timestamp outside the tolerance window — which stops an
attacker from replaying a captured webhook later.
"""

import hashlib
import hmac
import time

from app.core.exceptions import AppError

TOLERANCE_SECONDS = 300


class WebhookSignatureError(AppError):
    status_code = 400
    code = "invalid_signature"


def sign(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    t = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"


def verify(payload: bytes, header: str | None, secret: str, *, now: int | None = None) -> None:
    if not secret:
        raise WebhookSignatureError("Webhook secret is not configured")
    if not header:
        raise WebhookSignatureError("Missing signature")
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        key, _, value = item.strip().partition("=")
        parts.setdefault(key, []).append(value)
    try:
        timestamp = int(parts["t"][0])
    except (KeyError, ValueError) as exc:
        raise WebhookSignatureError("Malformed signature header") from exc
    current = int(time.time()) if now is None else now
    if abs(current - timestamp) > TOLERANCE_SECONDS:
        raise WebhookSignatureError("Signature timestamp outside the allowed window")
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in parts.get("v1", [])):
        raise WebhookSignatureError("Signature does not match")
