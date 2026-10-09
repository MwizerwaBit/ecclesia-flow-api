"""Authentication & authorization hardening — end to end against the dev DB.

Covers: per-account lockout, refresh-token cookie transport + CSRF header,
token tampering, session freshness (role change / suspension), invitations,
branch-scoped ABAC on members/groups/hierarchy, privilege-escalation guards,
and the MFA requirement on sensitive permissions.
"""

import time

import jwt
import pytest

from app.core.authz import Scope
from app.core.config import get_settings
from app.core.database import tenant_session
from app.core.deps import require_permission
from app.core.exceptions import ForbiddenError
from app.core.security import AccessTokenClaims
from app.modules.rbac.models import SYSTEM_ROLE_BOARD_ID, SYSTEM_ROLE_STAFF_ID


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _register(client, church_payload, label):
    payload = church_payload(label)
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200
    return payload, resp.json()


async def _promote_to_board(session: dict) -> None:
    membership = session["active_membership"]
    async with tenant_session(membership["tenant_id"], session["user"]["id"]) as tx:
        await tx.execute_raw(
            "update tenant_memberships set role_id = $1::uuid where id = $2::uuid",
            SYSTEM_ROLE_BOARD_ID,
            membership["id"],
        )


# ───────────────────────────── Authentication ─────────────────────────────


async def test_account_locks_after_repeated_failures_without_leaking_to_guessers(client, church_payload):
    payload, _ = await _register(client, church_payload, "lockout")
    bad = {"email": payload["email"], "password": "WrongPassword9"}
    for _ in range(get_settings().max_failed_logins):
        assert (await client.post("/api/v1/auth/login", json=bad)).status_code == 401

    # A guesser still sees the generic error...
    guess = await client.post("/api/v1/auth/login", json=bad)
    assert guess.status_code == 401
    assert guess.json()["error"]["code"] == "unauthorized"
    # ...only the real password holder learns the account is locked.
    real = await client.post("/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]})
    assert real.status_code == 423
    assert real.json()["error"]["code"] == "account_locked"
    assert real.json()["error"]["lockedUntil"]


async def test_browser_refresh_token_is_httponly_cookie_and_needs_csrf_header(browser, church_payload):
    payload = church_payload("cookie")
    reg = await browser.post("/api/v1/auth/register", json=payload)
    assert reg.status_code == 200
    assert reg.json()["refresh_token"] is None  # never visible to page JavaScript
    set_cookie = reg.headers["set-cookie"].lower()
    assert "ef_refresh=" in set_cookie
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie and "path=/api/v1/auth" in set_cookie
    assert reg.headers["cache-control"] == "no-store"

    no_header = await browser.post("/api/v1/auth/refresh")
    assert no_header.status_code == 403
    assert no_header.json()["error"]["code"] == "csrf"

    ok = await browser.post("/api/v1/auth/refresh", headers={"X-Requested-With": "XMLHttpRequest"})
    assert ok.status_code == 200
    assert ok.json()["access_token"]

    out = await browser.post("/api/v1/auth/logout", headers={"X-Requested-With": "XMLHttpRequest"})
    assert out.status_code == 204
    after = await browser.post("/api/v1/auth/refresh", headers={"X-Requested-With": "XMLHttpRequest"})
    assert after.status_code == 401


async def test_tampered_or_foreign_tokens_are_rejected(client, church_payload):
    _, session = await _register(client, church_payload, "tamper")
    claims = jwt.decode(session["access_token"], options={"verify_signature": False})
    settings = get_settings()

    wrong_audience = jwt.encode({**claims, "aud": "some-other-service"}, settings.jwt_secret, algorithm="HS256")
    escalated = jwt.encode({**claims, "permissions": ["members:export"]}, "not-the-secret", algorithm="HS256")
    unsigned = jwt.encode({**claims}, key=None, algorithm="none")
    for token in (wrong_audience, escalated, unsigned):
        resp = await client.get("/api/v1/members", headers=_auth(token))
        assert resp.status_code == 401, token


async def test_password_login_session_is_not_mfa_verified(client, church_payload):
    payload, _ = await _register(client, church_payload, "mfaflag")
    login = await client.post("/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]})
    claims = jwt.decode(login.json()["access_token"], options={"verify_signature": False})
    assert claims["mfa_verified"] is False


async def test_sensitive_permissions_require_an_mfa_verified_session():
    now = int(time.time())
    base = dict(sub="u", jti="j", iat=now, exp=now + 60, permissions=["members:export", "members:read"])
    check = require_permission("members:export")
    with pytest.raises(ForbiddenError) as exc:
        await check(AccessTokenClaims(**base, mfa_verified=False))
    assert exc.value.code == "mfa_required"
    assert await check(AccessTokenClaims(**base, mfa_verified=True))
    # Ordinary permissions don't need MFA.
    assert await require_permission("members:read")(AccessTokenClaims(**base, mfa_verified=False))


# ───────────────────────────── Session freshness ─────────────────────────────


async def test_role_change_invalidates_existing_access_tokens(client, church_payload):
    _, session = await _register(client, church_payload, "stale")
    assert (await client.get("/api/v1/members", headers=_auth(session["access_token"]))).status_code == 200

    await _promote_to_board(session)
    stale = await client.get("/api/v1/members", headers=_auth(session["access_token"]))
    assert stale.status_code == 401
    assert stale.json()["error"]["code"] == "session_stale"

    refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})
    assert refreshed.status_code == 200
    assert "team:invite" in refreshed.json()["permissions"]
    assert (await client.get("/api/v1/members", headers=_auth(refreshed.json()["access_token"]))).status_code == 200


# ───────────────────────────── Invitations + branch-scoped ABAC ─────────────────────────────


async def _board_church_with_branch(client, church_payload, label):
    """A church whose leader is board-level, with one branch unit under the root."""
    _, session = await _register(client, church_payload, label)
    await _promote_to_board(session)
    session = (await client.post("/api/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})).json()
    lead = _auth(session["access_token"])

    units = (await client.get("/api/v1/hierarchy/units", headers=lead)).json()
    root_id = units[0]["id"]
    branch = await client.post(
        "/api/v1/hierarchy/units", json={"name": "Eastside", "type": "Branch", "parent_id": root_id}, headers=lead
    )
    assert branch.status_code == 200, branch.text
    return session, lead, root_id, branch.json()["id"]


def _person(first: str, unit_id: str) -> dict:
    return {
        "first_name": first,
        "last_name": "Test",
        "gender": "female",
        "phone": f"+25078{abs(hash(first)) % 10_000_000:07d}",
        "unit_id": unit_id,
        "consent": {"given_by": "self"},
        "national_id": f"ID{abs(hash((first, unit_id))) % 10**10:010d}",
    }


async def test_invited_branch_staff_only_ever_reach_their_branch(client, church_payload):
    session, lead, root_id, branch_id = await _board_church_with_branch(client, church_payload, "abac")

    in_branch = (await client.post("/api/v1/members", json=_person("Branchy", branch_id), headers=lead)).json()
    at_root = (await client.post("/api/v1/members", json=_person("Rooty", root_id), headers=lead)).json()

    invite = await client.post(
        "/api/v1/team/invite",
        json={
            "email": f"scoped.{session['user']['id'][:8]}@example-church.org",
            "role_id": SYSTEM_ROLE_STAFF_ID,
            "unit_scope": branch_id,
        },
        headers=lead,
    )
    assert invite.status_code == 200, invite.text
    token = invite.json()["invite_url"].split("#", 1)[1]

    accepted = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": token, "password": "BranchStaff2026", "first_name": "Bea", "last_name": "Scoped"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["unit_scope_id"] == branch_id
    reused = await client.post("/api/v1/auth/accept-invite", json={"token": token, "password": "BranchStaff2026"})
    assert reused.status_code == 401  # single use

    staff = _auth(accepted.json()["access_token"])

    # Members: list, read, write — branch only; out-of-scope answers 404.
    listed = {m["id"] for m in (await client.get("/api/v1/members", headers=staff)).json()}
    assert listed == {in_branch["id"]}
    assert (await client.get(f"/api/v1/members/{at_root['id']}", headers=staff)).status_code == 404
    assert (
        await client.patch(f"/api/v1/members/{at_root['id']}", json={"notes": "x"}, headers=staff)
    ).status_code == 404
    assert (await client.get(f"/api/v1/members/{in_branch['id']}", headers=staff)).status_code == 200

    # New registrations land in the branch; placing someone outside it is refused.
    unplaced = _person("Newbie", branch_id)
    del unplaced["unit_id"]
    created = await client.post("/api/v1/members", json=unplaced, headers=staff)
    assert created.json()["unit_id"] == branch_id
    outside = await client.post("/api/v1/members", json=_person("Outsider", root_id), headers=staff)
    assert outside.status_code == 403
    assert outside.json()["error"]["code"] == "out_of_scope"

    # Groups: church-wide readable but not editable; branch groups manageable;
    # only branch members can be added to a roster.
    church_wide = (await client.post("/api/v1/groups", json={"name": "Choir"}, headers=lead)).json()
    branch_group = (
        await client.post("/api/v1/groups", json={"name": "East Cell", "unit_id": branch_id}, headers=lead)
    ).json()
    assert {g["id"] for g in (await client.get("/api/v1/groups", headers=staff)).json()} == {
        church_wide["id"],
        branch_group["id"],
    }
    assert (
        await client.patch(f"/api/v1/groups/{church_wide['id']}", json={"name": "X"}, headers=staff)
    ).status_code == 403
    added = await client.post(
        f"/api/v1/groups/{branch_group['id']}/members",
        json={"member_ids": [in_branch["id"], at_root["id"]]},
        headers=staff,
    )
    assert [m["member_id"] for m in added.json()] == [in_branch["id"]]

    # Hierarchy: can't edit or delete the branch they are scoped to, nor anything above it.
    assert (
        await client.patch(f"/api/v1/hierarchy/units/{branch_id}", json={"name": "Mine"}, headers=staff)
    ).status_code == 403
    assert (await client.delete(f"/api/v1/hierarchy/units/{root_id}", headers=staff)).status_code == 403


async def test_suspension_and_escalation_guards(client, church_payload):
    session, lead, _, branch_id = await _board_church_with_branch(client, church_payload, "suspend")
    invite = await client.post(
        "/api/v1/team/invite",
        json={"email": f"susp.{session['user']['id'][:8]}@example-church.org", "role_id": SYSTEM_ROLE_STAFF_ID},
        headers=lead,
    )
    membership_id = invite.json()["id"]
    token = invite.json()["invite_url"].split("#", 1)[1]
    staff_session = (
        await client.post("/api/v1/auth/accept-invite", json={"token": token, "password": "Suspended2026"})
    ).json()
    staff = _auth(staff_session["access_token"])
    assert (await client.get("/api/v1/members", headers=staff)).status_code == 200

    # Nobody edits their own access, and the leader changes only via transfer.
    own = await client.patch(
        f"/api/v1/team/{session['active_membership']['id']}", json={"status": "suspended"}, headers=lead
    )
    assert own.status_code == 403

    suspended = await client.patch(f"/api/v1/team/{membership_id}", json={"status": "suspended"}, headers=lead)
    assert suspended.status_code == 200
    blocked = await client.get("/api/v1/members", headers=staff)
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "session_revoked"

    # Creating roles is MFA-gated; this password-only session can't.
    role = await client.post("/api/v1/roles", json={"name": "Helpers", "permissions": ["members:read"]}, headers=lead)
    assert role.status_code == 403
    assert role.json()["error"]["code"] == "mfa_required"


def test_scope_rules():
    branch = Scope(
        user_id="u",
        unit_ids=frozenset({"east", "east-cell"}),
        root_unit_id="east",
        permissions=frozenset({"members:read"}),
        is_platform_admin=False,
    )
    assert branch.unit_visible("east-cell") and not branch.unit_visible("west") and not branch.unit_visible(None)
    assert branch.shared_readable(None) and not branch.unit_writable(None)
    assert branch.assignable_unit(None) == "east"
    with pytest.raises(ForbiddenError):
        branch.assignable_unit("west")

    from app.core.authz import ensure_grantable

    ensure_grantable(branch, ["members:read"])
    with pytest.raises(ForbiddenError) as exc:
        ensure_grantable(branch, ["members:read", "finance:read"])
    assert exc.value.code == "privilege_escalation"
