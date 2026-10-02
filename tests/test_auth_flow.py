"""Integration tests for the identity & tenant module (plan.md Phase 0/1).

Runs against the real dev Postgres (migrations already applied) rather than
an isolated per-test database — each test generates its own unique
email/church name via conftest's `church_payload` fixture so tests don't
collide, which is a pragmatic tradeoff for this pass rather than standing up
a fully ephemeral test database. A follow-up could move this to a
transactional-rollback-per-test fixture against a dedicated test schema.
"""
import pyotp


async def test_register_rejects_weak_password(client, church_payload):
    payload = church_payload("weakpw")
    payload["password"] = "alllowercase1"  # no uppercase
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 422


async def test_register_rejects_duplicate_email(client, church_payload):
    payload = church_payload("dup")
    first = await client.post("/api/v1/auth/register", json=payload)
    assert first.status_code == 200

    payload2 = church_payload("dup2")
    payload2["email"] = payload["email"]
    second = await client.post("/api/v1/auth/register", json=payload2)
    assert second.status_code == 403


async def test_register_assigns_staff_role_and_leadership(client, church_payload):
    payload = church_payload("leader")
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "staff"
    assert body["active_membership"]["is_leader"] is True
    assert body["active_membership"]["is_primary"] is True
    assert "members:export" not in body["permissions"]  # board-only permission
    assert "members:read" in body["permissions"]


async def test_login_wrong_password_and_nonexistent_email_match_exactly(client, church_payload):
    payload = church_payload("loginfail")
    await client.post("/api/v1/auth/register", json=payload)

    wrong_pw = await client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": "WrongPassword9"}
    )
    nonexistent = await client.post(
        "/api/v1/auth/login", json={"email": "nobody-" + payload["email"], "password": "WrongPassword9"}
    )
    assert wrong_pw.status_code == 401
    assert nonexistent.status_code == 401
    # Same error shape for both — a client can't distinguish "wrong password"
    # from "no such account" and enumerate valid emails.
    assert wrong_pw.json()["error"]["code"] == nonexistent.json()["error"]["code"]
    assert wrong_pw.json()["error"]["message"] == nonexistent.json()["error"]["message"]


async def test_refresh_rotation_and_reuse_burns_whole_family(client, church_payload):
    payload = church_payload("refresh")
    reg = await client.post("/api/v1/auth/register", json=payload)
    old_refresh = reg.json()["refresh_token"]

    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert rotated.status_code == 200
    new_refresh = rotated.json()["refresh_token"]
    assert new_refresh != old_refresh

    # Reusing the superseded token is theft-shaped: rejected...
    reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert reuse.status_code == 401

    # ...and the fix under test: the CURRENT token from the legitimate
    # rotation must ALSO be dead afterward, not just the replayed one.
    new_token_after_theft = await client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh})
    assert new_token_after_theft.status_code == 401


async def test_mfa_setup_verify_challenge_and_backup_code_is_single_use(client, church_payload):
    payload = church_payload("mfa")
    reg = await client.post("/api/v1/auth/register", json=payload)
    access = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}

    setup = await client.post("/api/v1/auth/mfa/setup", headers=headers)
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    backup_code = setup.json()["backup_codes"][0]

    code = pyotp.TOTP(secret).now()
    verify = await client.post("/api/v1/auth/mfa/verify", headers=headers, json={"code": code})
    assert verify.status_code == 204

    # Login now demands the MFA challenge instead of a session.
    login = await client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    assert login.status_code == 200
    assert login.json().get("mfa_required") is True
    challenge_token = login.json()["challenge_token"]

    wrong = await client.post(
        "/api/v1/auth/mfa/challenge", json={"challenge_token": challenge_token, "code": "000000"}
    )
    assert wrong.status_code == 401

    used_once = await client.post(
        "/api/v1/auth/mfa/challenge", json={"challenge_token": challenge_token, "code": backup_code}
    )
    assert used_once.status_code == 200

    # Same backup code must not work a second time, even against a fresh challenge.
    login2 = await client.post(
        "/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]}
    )
    challenge_token2 = login2.json()["challenge_token"]
    reused = await client.post(
        "/api/v1/auth/mfa/challenge", json={"challenge_token": challenge_token2, "code": backup_code}
    )
    assert reused.status_code == 401


async def test_step_up_gates_mfa_disable(client, church_payload):
    payload = church_payload("stepup")
    reg = await client.post("/api/v1/auth/register", json=payload)
    access = reg.json()["access_token"]
    headers = {"Authorization": f"Bearer {access}"}

    setup = await client.post("/api/v1/auth/mfa/setup", headers=headers)
    secret = setup.json()["secret"]
    code = pyotp.TOTP(secret).now()
    await client.post("/api/v1/auth/mfa/verify", headers=headers, json={"code": code})

    without_step_up = await client.post("/api/v1/auth/mfa/disable", headers=headers)
    assert without_step_up.status_code == 403
    assert without_step_up.json()["error"]["code"] == "step_up_required"

    step_up_code = pyotp.TOTP(secret).now()
    step_up = await client.post("/api/v1/auth/step-up", headers=headers, json={"code": step_up_code})
    assert step_up.status_code == 200
    step_up_token = step_up.json()["step_up_token"]

    with_step_up = await client.post(
        "/api/v1/auth/mfa/disable",
        headers={**headers, "X-Step-Up-Token": step_up_token},
    )
    assert with_step_up.status_code == 204


async def test_tenant_switch_rejects_other_users_membership(client, church_payload):
    a = await client.post("/api/v1/auth/register", json=church_payload("tenanta"))
    b = await client.post("/api/v1/auth/register", json=church_payload("tenantb"))
    a_access = a.json()["access_token"]
    b_membership_id = b.json()["active_membership"]["id"]

    resp = await client.post(
        "/api/v1/auth/switch-tenant",
        headers={"Authorization": f"Bearer {a_access}"},
        json={"membership_id": b_membership_id},
    )
    assert resp.status_code == 404


async def test_church_directory_hides_suspended_orgs(client, church_payload):
    reg = await client.post("/api/v1/auth/register", json=church_payload("directory"))
    slug_resp = await client.get("/api/v1/churches", params={"q": reg.json()["active_membership"]["tenant_name"]})
    assert slug_resp.status_code == 200
    assert len(slug_resp.json()) == 1
