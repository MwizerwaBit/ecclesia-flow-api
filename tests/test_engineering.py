"""Engineering foundation (TODO.md Phase 4) — end to end against the dev DB.

Covers: account recovery (DIF-07), request-size limits (DIF-05), safe media
uploads, pagination (DIF-06) and the tamper-evident audit log (DIF-08).
"""

import asyncio
import re
import secrets

import pyotp
import pytest
from prisma import Prisma
from prisma.errors import PrismaError

from app.core.config import get_settings
from app.core.database import platform_client, platform_session, tenant_session
from app.core.mailer import get_mailer
from app.modules.audit.integrity import chain_breaks, create_checkpoint, verify_checkpoint
from app.modules.audit.service import write_audit
from tests.conftest import adult_member, auth, unique_email

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def _register(client, church_payload, label):
    payload = church_payload(label)
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200, resp.text
    return payload, resp.json()


def _emails_to(address: str) -> list:
    return [m for m in get_mailer().outbox if m.to == address]


def _token_from(email) -> str:
    return re.search(r"/reset-password#(\S+)", email.body).group(1)


# ───────────────────────────── DIF-07 account recovery ─────────────────────────────


async def test_password_reset_is_single_use_and_ends_existing_sessions(client, church_payload):
    payload, session = await _register(client, church_payload, "reset")
    email = payload["email"]

    known = await client.post("/api/v1/auth/password-reset/request", json={"email": email})
    unknown = await client.post("/api/v1/auth/password-reset/request", json={"email": unique_email("nobody")})
    # No account enumeration: identical status and body.
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert len(_emails_to(email)) == 1
    first = _token_from(_emails_to(email)[0])
    assert first not in str(known.json())

    # A newer request voids the older link.
    await client.post("/api/v1/auth/password-reset/request", json={"email": email})
    second = _token_from(_emails_to(email)[-1])
    new_password = "Recovered2026Pass"
    voided = await client.post("/api/v1/auth/password-reset/complete", json={"token": first, "password": new_password})
    assert voided.status_code == 401 and voided.json()["error"]["code"] == "reset_invalid"

    guessed = await client.post(
        "/api/v1/auth/password-reset/complete", json={"token": secrets.token_urlsafe(32), "password": new_password}
    )
    assert guessed.status_code == 401
    malformed = await client.post("/api/v1/auth/password-reset/complete", json={"token": "x", "password": new_password})
    assert malformed.status_code == 422
    weak = await client.post("/api/v1/auth/password-reset/complete", json={"token": second, "password": "password12"})
    assert weak.status_code == 422

    await asyncio.sleep(1.1)  # tokens carry whole-second iat; see deps._ensure_session_current
    done = await client.post("/api/v1/auth/password-reset/complete", json={"token": second, "password": new_password})
    assert done.status_code == 204, done.text
    reused = await client.post("/api/v1/auth/password-reset/complete", json={"token": second, "password": new_password})
    assert reused.status_code == 401

    # Every existing session ends.
    old_access = await client.get("/api/v1/members", headers=auth(session["access_token"]))
    assert old_access.status_code == 401 and old_access.json()["error"]["code"] == "session_revoked"
    old_refresh = await client.post("/api/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert old_refresh.status_code == 401

    assert (
        await client.post("/api/v1/auth/login", json={"email": email, "password": payload["password"]})
    ).status_code == 401
    assert (await client.post("/api/v1/auth/login", json={"email": email, "password": new_password})).status_code == 200

    # The reset — and the stale-password login attempt just above, now that
    # failed logins are audited too (SA-12) — are in the account-level audit
    # chain, which still verifies.
    async with platform_session() as db:
        rows = await db.query_raw(
            "select action from audit_logs where tenant_id is null and resource_id = $1::uuid order by chain_seq",
            session["user"]["id"],
        )
        assert [r["action"] for r in rows] == [
            "account.password_reset_requested",
            "account.password_reset_requested",
            "account.password_reset_completed",
            "auth.login_failed",
        ]
        assert await chain_breaks(db, None) == []


async def test_reset_links_expire_and_emails_are_capped_per_account(client, church_payload):
    payload, session = await _register(client, church_payload, "reset-cap")
    email = payload["email"]
    cap = get_settings().password_reset_max_per_hour
    for _ in range(cap + 1):
        resp = await client.post("/api/v1/auth/password-reset/request", json={"email": email})
        assert resp.status_code == 202
    assert len(_emails_to(email)) == cap  # the extra request answered the same, sent nothing

    token = _token_from(_emails_to(email)[-1])
    async with platform_session() as db:
        await db.execute_raw(
            "update password_reset_tokens set expires_at = now() - interval '1 minute' where user_id = $1::uuid",
            session["user"]["id"],
        )
    expired = await client.post(
        "/api/v1/auth/password-reset/complete", json={"token": token, "password": "Recovered2026Pass"}
    )
    assert expired.status_code == 401 and expired.json()["error"]["code"] == "reset_invalid"


# ───────────────────────────── DIF-05 request size ─────────────────────────────


async def test_oversized_bodies_are_refused_with_413(client):
    limit = get_settings().max_request_body_bytes
    declared = await client.post(
        "/api/v1/auth/login",
        content=b'{"email": "' + b"a" * (limit + 10) + b'"}',
        headers={"content-type": "application/json"},
    )
    assert declared.status_code == 413
    assert declared.json()["error"]["code"] == "payload_too_large"
    assert declared.headers.get("x-request-id")

    async def chunks():
        for _ in range(limit // 65536 + 2):
            yield b"a" * 65536

    chunked = await client.post("/api/v1/auth/login", content=chunks(), headers={"content-type": "application/json"})
    assert chunked.status_code == 413

    normal = await client.post("/api/v1/auth/login", json={"email": unique_email("x"), "password": "Whatever2026"})
    assert normal.status_code == 401


# ───────────────────────────── Media uploads ─────────────────────────────


async def test_media_uploads_are_typed_by_their_bytes(client, church_payload):
    _, session = await _register(client, church_payload, "media")
    h = auth(session["access_token"])

    html = await client.post(
        "/api/v1/media",
        files={"file": ("evil.html", b"<html><script>alert(1)</script></html>", "image/png")},
        headers=h,
    )
    assert html.status_code == 415
    svg = await client.post(
        "/api/v1/media", files={"file": ("logo.svg", b"<svg onload='alert(1)'/>", "image/svg+xml")}, headers=h
    )
    assert svg.status_code == 415

    # A real PNG named .html is stored and served as a PNG.
    png = await client.post("/api/v1/media", files={"file": ("photo.html", PNG, "text/html")}, headers=h)
    assert png.status_code == 200, png.text
    assert png.json()["url"].endswith(".png") and png.json()["mime_type"] == "image/png"
    served = await client.get(png.json()["url"])
    assert served.status_code == 200
    assert "sandbox" in served.headers["content-security-policy"]
    assert served.headers["x-content-type-options"] == "nosniff"

    big = PNG + b"\x00" * get_settings().max_document_bytes
    too_big = await client.post("/api/v1/media", files={"file": ("big.png", big, "image/png")}, headers=h)
    assert too_big.status_code == 413


# ───────────────────────────── DIF-06 pagination ─────────────────────────────


async def test_collections_are_paginated_with_metadata(client, church_payload):
    _, session = await _register(client, church_payload, "pages")
    h = auth(session["access_token"])
    for i in range(5):
        assert (await client.post("/api/v1/members", json=adult_member(f"Page{i}"), headers=h)).status_code == 200

    first = await client.get("/api/v1/members", params={"limit": 2}, headers=h)
    assert first.status_code == 200 and len(first.json()) == 2
    assert first.headers["x-total-count"] == "5" and first.headers["x-page-limit"] == "2"
    assert 'rel="next"' in first.headers["link"] and 'rel="prev"' not in first.headers["link"]

    pages = [first.json()]
    for offset in (2, 4):
        pages.append((await client.get("/api/v1/members", params={"limit": 2, "offset": offset}, headers=h)).json())
    ids = [m["id"] for page in pages for m in page]
    assert len(ids) == len(set(ids)) == 5  # deterministic order: no overlap, nothing skipped
    last = await client.get("/api/v1/members", params={"limit": 2, "offset": 4}, headers=h)
    assert 'rel="prev"' in last.headers["link"] and 'rel="next"' not in last.headers["link"]

    assert (await client.get("/api/v1/members", params={"limit": 501}, headers=h)).status_code == 422
    assert (await client.get("/api/v1/members", params={"offset": -1}, headers=h)).status_code == 422
    default = await client.get("/api/v1/members", headers=h)
    assert default.headers["x-page-limit"] == "100"


# ───────────────────────────── DIF-08 audit integrity ─────────────────────────────


async def test_audit_log_is_append_only_and_tampering_is_detected(client, church_payload):
    _, session = await _register(client, church_payload, "audit")
    tenant_id, user_id = session["active_membership"]["tenant_id"], session["user"]["id"]
    async with tenant_session(tenant_id, user_id) as tx:
        for i in range(3):
            await write_audit(
                tx,
                tenant_id=tenant_id,
                actor_user_id=user_id,
                action=f"test.entry_{i}",
                resource_type="organization",
                resource_id=tenant_id,
            )

    # Neither app role can change or remove history.
    for statement in ("update audit_logs set action = 'forged'", "delete from audit_logs"):
        with pytest.raises(PrismaError):
            async with tenant_session(tenant_id, user_id) as tx:
                await tx.execute_raw(statement + " where tenant_id = $1::uuid", tenant_id)
        with pytest.raises(PrismaError):
            async with platform_session() as db:
                await db.execute_raw(statement + " where tenant_id = $1::uuid", tenant_id)

    key = "k" * 40
    async with platform_session() as db:
        rows = await db.query_raw(
            "select chain_seq, prev_hash, row_hash from audit_logs where tenant_id = $1::uuid order by chain_seq",
            tenant_id,
        )
        assert [int(r["chain_seq"]) for r in rows] == list(range(1, len(rows) + 1))
        assert all(rows[i]["prev_hash"] == rows[i - 1]["row_hash"] for i in range(1, len(rows)))
        assert await chain_breaks(db, tenant_id) == []
    # Checkpoints run on the plain platform client, like scripts/audit_checkpoint.py.
    checkpoint = await create_checkpoint(platform_client, key, tenant_ids=[tenant_id])
    assert [h["tenant_id"] for h in checkpoint["heads"]] == [tenant_id]
    assert await verify_checkpoint(platform_client, key, checkpoint) == []
    forged = {**checkpoint, "heads": [{**h, "row_hash": "0" * 64} for h in checkpoint["heads"]]}
    assert "signature is invalid" in (await verify_checkpoint(platform_client, key, forged))[0]

    # Even the database owner, after deliberately disabling the guard, can't
    # edit an entry without the chain showing exactly where.
    owner = Prisma(datasource={"url": get_settings().database_url})
    await owner.connect()
    try:
        with pytest.raises(RuntimeError, match="rolled back"):
            async with owner.tx() as tx:
                await tx.execute_raw("alter table audit_logs disable trigger audit_logs_no_update_delete")
                await tx.execute_raw(
                    "update audit_logs set action = 'forged' where tenant_id = $1::uuid and chain_seq = 2", tenant_id
                )
                breaks = await tx.query_raw("select * from verify_audit_chain($1::uuid)", tenant_id)
                assert breaks == [{"broken_at_seq": 2, "reason": "content changed"}]
                raise RuntimeError("rolled back")
    finally:
        await owner.disconnect()
    async with platform_session() as db:
        assert await chain_breaks(db, tenant_id) == []


# ───────────────────────────── SA-12 failed logins & authz denials ─────────────────────────────


async def test_failed_logins_and_lockout_are_audited(client, church_payload):
    payload, session = await _register(client, church_payload, "failed-login")
    user_id = session["user"]["id"]
    settings = get_settings()

    for _ in range(settings.max_failed_logins):
        wrong = await client.post(
            "/api/v1/auth/login", json={"email": payload["email"], "password": "DefinitelyWrong123!"}
        )
        assert wrong.status_code == 401

    async with platform_session() as db:
        rows = await db.query_raw(
            "select action from audit_logs where tenant_id is null and resource_id = $1::uuid order by chain_seq",
            user_id,
        )
        actions = [r["action"] for r in rows]
        assert actions.count("auth.login_failed") == settings.max_failed_logins
        assert actions[-1] == "auth.account_locked"
        assert await chain_breaks(db, None) == []

    locked = await client.post("/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]})
    assert locked.status_code == 423


async def test_authorization_denials_are_audited(client, church_payload):
    from app.modules.rbac.models import SYSTEM_ROLE_MEMBER_ID
    from tests.conftest import accept_invite

    payload, session = await _register(client, church_payload, "authz-denied")
    tenant_id = session["active_membership"]["tenant_id"]
    lead = auth(session["access_token"])

    invite = await client.post(
        "/api/v1/team/invite", json={"email": unique_email("member"), "role_id": SYSTEM_ROLE_MEMBER_ID}, headers=lead
    )
    assert invite.status_code == 200, invite.text
    accepted = await accept_invite(client, invite.json()["invite_url"])
    member = auth(accepted["access_token"])

    denied = await client.get("/api/v1/members", headers=member)
    assert denied.status_code == 403

    async with platform_session() as db:
        rows = await db.query_raw(
            "select actor_user_id, metadata from audit_logs"
            " where tenant_id = $1::uuid and action = $2 order by chain_seq",
            tenant_id,
            "authz.permission_denied",
        )
        assert rows[-1]["actor_user_id"] == accepted["user"]["id"]
        assert rows[-1]["metadata"]["path"] == "/api/v1/members"
        assert await chain_breaks(db, tenant_id) == []


# ───────────────────────────── SA-10 MFA challenge single-use ─────────────────────────────


async def test_mfa_challenge_tokens_cannot_be_replayed_after_success(client, church_payload):
    payload, session = await _register(client, church_payload, "mfa-single-use")
    h = auth(session["access_token"])
    setup = (await client.post("/api/v1/auth/mfa/setup", headers=h)).json()
    secret = setup["secret"]
    verify = await client.post("/api/v1/auth/mfa/verify", headers=h, json={"code": pyotp.TOTP(secret).now()})
    assert verify.status_code == 204, verify.text

    login = await client.post("/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]})
    challenge = login.json()["challenge_token"]

    done = await client.post(
        "/api/v1/auth/mfa/challenge", json={"challenge_token": challenge, "code": pyotp.TOTP(secret).now()}
    )
    assert done.status_code == 200, done.text

    # Same token, a fresh and otherwise-correct code: still rejected, because
    # the token itself — not just that one code — was consumed on success.
    replayed = await client.post(
        "/api/v1/auth/mfa/challenge", json={"challenge_token": challenge, "code": pyotp.TOTP(secret).now()}
    )
    assert replayed.status_code == 401
    assert replayed.json()["error"]["code"] == "mfa_challenge_invalid"


async def test_mfa_challenge_attempts_are_capped_per_token():
    """Exercised at the repository directly, not over HTTP: the default
    per-token cap and the account lockout threshold are both 5, so driving
    this through /auth/mfa/challenge with wrong codes would lock the account
    on the same attempt that exhausts the token, testing lockout instead of
    the thing this test is for."""
    from datetime import UTC, datetime, timedelta

    from app.modules.identity import repository

    settings = get_settings()
    async with platform_session() as db:
        user = await db.user.create(
            data={"email": unique_email("mfa-cap"), "first_name": "Cap", "last_name": "Test", "mfa_enabled": True}
        )
        jti = secrets.token_urlsafe(8)
        expires_at = datetime.now(UTC) + timedelta(minutes=5)

        for expected in range(1, settings.mfa_challenge_max_attempts + 1):
            attempt = await repository.claim_mfa_attempt(db, jti=jti, user_id=user.id, expires_at=expires_at)
            assert attempt == expected

        # One more claim past the cap: the caller is expected to burn it here.
        attempt = await repository.claim_mfa_attempt(db, jti=jti, user_id=user.id, expires_at=expires_at)
        assert attempt == settings.mfa_challenge_max_attempts + 1
        await repository.burn_mfa_attempt(db, jti)

        # Burned: every further claim reports "already gone".
        assert await repository.claim_mfa_attempt(db, jti=jti, user_id=user.id, expires_at=expires_at) is None


# ───────────────────────────── DIF-11 observability ─────────────────────────────


def test_logs_are_json_and_scrubbed():
    import json
    import logging

    from app.core.logging import JsonFormatter, scrub

    jwt_like = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLXZhbHVl"
    record = logging.makeLogRecord(
        {
            "name": "t",
            "levelname": "INFO",
            "msg": f"login for alice@example.org with Bearer {jwt_like} and token {secrets.token_urlsafe(32)}",
            "password": "Hunter2Hunter2",
            "user_id": "3f1c2a4e-1b2c-4d5e-8f90-123456789abc",
        }
    )
    line = json.loads(JsonFormatter().format(record))
    assert "alice@" not in line["msg"] and "***@example.org" in line["msg"]
    assert jwt_like not in line["msg"] and "[REDACTED" in line["msg"]
    assert line["password"] == "[REDACTED]"
    assert line["user_id"] == "3f1c2a4e-1b2c-4d5e-8f90-123456789abc"  # ids stay for correlation
    assert scrub({"nested": {"refresh_token": "abc"}}) == {"nested": {"refresh_token": "[REDACTED]"}}


async def test_health_checks_and_request_ids(client):
    assert (await client.get("/health")).json() == {"status": "ok"}
    ready = await client.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["database"] == "up"
    kept = await client.get("/health", headers={"x-request-id": "abc-123"})
    assert kept.headers["x-request-id"] == "abc-123"
    injected = await client.get("/health", headers={"x-request-id": "bad\nvalue" + "x" * 100})
    assert injected.headers["x-request-id"] != "bad\nvalue" + "x" * 100
