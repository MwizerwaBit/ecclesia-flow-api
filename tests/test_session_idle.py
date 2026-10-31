"""Idle sign-out limits: 15 minutes for leader-side sessions (the church can
change it), an hour for members, fifteen for platform admins."""

from app.core.session_policy import idle_minutes
from app.modules.rbac.models import SYSTEM_ROLE_MEMBER_ID, SYSTEM_ROLE_STAFF_ID
from tests.conftest import accept_invite, auth, make_platform_admin, unique_email
from tests.test_foundation import _mfa


def test_policy_table():
    leader = {"is_platform_admin": False, "is_member_role": False}
    assert idle_minutes(**leader, church_minutes=None) == 15
    assert idle_minutes(**leader, church_minutes=30) == 30
    assert idle_minutes(**leader, church_minutes=1) == 5  # never under the floor
    assert idle_minutes(**leader, church_minutes=9999) == 120
    assert idle_minutes(is_platform_admin=False, is_member_role=True, church_minutes=5) == 60
    assert idle_minutes(is_platform_admin=True, is_member_role=False, church_minutes=120) == 15


async def _register(client, church_payload, label):
    payload = church_payload(label)
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200, resp.text
    return payload, resp.json()


async def _invite(client, leader_headers, role_id):
    invite = await client.post(
        "/api/v1/team/invite", json={"email": unique_email("idle"), "role_id": role_id}, headers=leader_headers
    )
    assert invite.status_code == 200, invite.text
    return await accept_invite(client, invite.json()["invite_url"])


async def test_sessions_carry_the_right_limit_and_the_church_can_change_it(client, church_payload):
    payload, session = await _register(client, church_payload, "idle")
    assert session["idle_timeout_minutes"] == 15  # a new church's leader

    # Changing it is an org setting: two-step verified, within bounds, audited.
    plain = auth(session["access_token"])
    refused = await client.put("/api/v1/org/security", json={"session_idle_minutes": 30}, headers=plain)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "mfa_required"

    verified = auth((await _mfa(client, payload, session))["access_token"])
    assert (await client.get("/api/v1/org/security", headers=verified)).json()["session_idle_minutes"] == 15
    for bad in (4, 121, 0, -5):
        assert (
            await client.put("/api/v1/org/security", json={"session_idle_minutes": bad}, headers=verified)
        ).status_code == 422
    done = await client.put("/api/v1/org/security", json={"session_idle_minutes": 30}, headers=verified)
    assert done.status_code == 200 and done.json()["session_idle_minutes"] == 30

    # Leader-side roles pick the new value up at their next sign-in; members stay at an hour.
    staff = await _invite(client, verified, SYSTEM_ROLE_STAFF_ID)
    assert staff["idle_timeout_minutes"] == 30
    member = await _invite(client, verified, SYSTEM_ROLE_MEMBER_ID)
    assert member["idle_timeout_minutes"] == 60

    # Staff can't change it (no org:settings), but can read it.
    staff_h = auth(staff["access_token"])
    assert (
        await client.put("/api/v1/org/security", json={"session_idle_minutes": 120}, headers=staff_h)
    ).status_code == 403


async def test_platform_admins_get_fifteen_minutes(client):
    admin = await make_platform_admin(client)
    assert admin["idle_timeout_minutes"] == 15
