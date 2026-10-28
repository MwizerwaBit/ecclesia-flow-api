"""Several scoped roles per login in one church.

The design these tests pin down: a person holds several assignments (role over a
unit scope) but a SESSION works under exactly one at a time, and moves between
them with a token switch. Scopes are never unioned, so a branch assignment can't
quietly widen into everything the person holds.
"""

from app.modules.rbac.models import SYSTEM_ROLE_BOARD_ID, SYSTEM_ROLE_STAFF_ID
from tests.conftest import accept_invite, adult_member, auth
from tests.test_features import _church


async def _branch(client, lead, name):
    units = (await client.get("/api/v1/hierarchy/units", headers=lead)).json()
    root = next(u for u in units if u.get("parent_id") is None)
    resp = await client.post(
        "/api/v1/hierarchy/units", json={"name": name, "type": "Branch", "parent_id": root["id"]}, headers=lead
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()


async def _staff_in(client, lead, email, branch):
    invite = await client.post(
        "/api/v1/team/invite",
        json={"email": email, "role_id": SYSTEM_ROLE_STAFF_ID, "unit_scope": branch["id"]},
        headers=lead,
    )
    assert invite.status_code == 200, invite.text
    session = await accept_invite(client, invite.json()["invite_url"], first="Dual", last="Hat")
    return invite.json()["id"], session


async def _add(client, lead, membership_id, branch_id, role_id=SYSTEM_ROLE_STAFF_ID):
    return await client.post(
        f"/api/v1/team/{membership_id}/assignments",
        json={"role_id": role_id, "unit_scope": branch_id},
        headers=lead,
    )


async def _switch(client, session, membership_id):
    return await client.post(
        "/api/v1/auth/switch-assignment",
        json={"membership_id": membership_id},
        headers=auth(session["access_token"]),
    )


async def test_one_login_holds_two_branch_roles_and_works_in_one_at_a_time(client, church_payload):
    _, _, lead = await _church(client, church_payload, "asg1")
    east, west = await _branch(client, lead, "East"), await _branch(client, lead, "West")
    east_member = (
        await client.post("/api/v1/members", json=adult_member("Eve", unit_id=east["id"]), headers=lead)
    ).json()
    west_member = (
        await client.post("/api/v1/members", json=adult_member("Wes", unit_id=west["id"]), headers=lead)
    ).json()

    membership_id, session = await _staff_in(client, lead, "dual.hat@example-church.org", east)
    assert session["unit_scope_id"] == east["id"]
    mine = auth(session["access_token"])

    # In the East assignment: East people only.
    assert (await client.get(f"/api/v1/members/{east_member['id']}", headers=mine)).status_code == 200
    assert (await client.get(f"/api/v1/members/{west_member['id']}", headers=mine)).status_code == 404

    added = await _add(client, lead, membership_id, west["id"])
    assert added.status_code == 201, added.text
    west_assignment = added.json()["id"]
    assert west_assignment != membership_id

    # The still-valid East token didn't widen: adding an assignment changes nothing for a session in flight.
    assert (await client.get(f"/api/v1/members/{west_member['id']}", headers=mine)).status_code == 404

    # The session lists both and switches to West, which flips what is visible.
    again = (await client.post("/api/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})).json()
    ids = {m["id"] for m in again["memberships"]}
    assert {membership_id, west_assignment} <= ids
    switched = await _switch(client, again, west_assignment)
    assert switched.status_code == 200, switched.text
    west_session = switched.json()
    assert west_session["unit_scope_id"] == west["id"]
    west_headers = auth(west_session["access_token"])
    assert (await client.get(f"/api/v1/members/{west_member['id']}", headers=west_headers)).status_code == 200
    assert (await client.get(f"/api/v1/members/{east_member['id']}", headers=west_headers)).status_code == 404

    # And back again.
    back = await _switch(client, west_session, membership_id)
    assert back.status_code == 200 and back.json()["unit_scope_id"] == east["id"]


async def test_you_can_only_switch_to_your_own_assignments_in_this_church(client, church_payload):
    _, _, lead = await _church(client, church_payload, "asg2")
    _, _, other_lead = await _church(client, church_payload, "asg2b")
    east, west = await _branch(client, lead, "East"), await _branch(client, lead, "West")
    other_branch = await _branch(client, other_lead, "Elsewhere")

    mine_id, mine = await _staff_in(client, lead, "one.hat@example-church.org", east)
    theirs_id, _ = await _staff_in(client, lead, "two.hat@example-church.org", west)
    foreign_id, _ = await _staff_in(client, other_lead, "far.hat@example-church.org", other_branch)

    assert (await _switch(client, mine, theirs_id)).status_code == 404  # another person's assignment
    assert (await _switch(client, mine, foreign_id)).status_code == 404  # another church's
    assert (await _switch(client, mine, "00000000-0000-4000-8000-000000000000")).status_code == 404
    assert (await _switch(client, mine, mine_id)).status_code == 200  # their own, trivially


async def test_adding_an_assignment_is_guarded_like_any_other_grant(client, church_payload):
    _, leader_session, lead = await _church(client, church_payload, "asg3")
    east, west = await _branch(client, lead, "East"), await _branch(client, lead, "West")
    membership_id, _ = await _staff_in(client, lead, "guard.hat@example-church.org", east)

    assert (await _add(client, lead, membership_id, west["id"])).status_code == 201
    assert (await _add(client, lead, membership_id, west["id"])).status_code == 409  # exact duplicate
    assert (await _add(client, lead, membership_id, east["id"])).status_code == 409  # their existing one
    # Administrators are appointed through leadership, never as an extra hat.
    assert (await _add(client, lead, membership_id, None, role_id=SYSTEM_ROLE_BOARD_ID)).status_code == 403
    # A role that doesn't exist.
    assert (
        await _add(client, lead, membership_id, west["id"], role_id="00000000-0000-4000-8000-0000000000aa")
    ).status_code == 404

    # The leader's own access changes only through a leadership transfer, and you can't give yourself more.
    leader_membership = leader_session["active_membership"]["id"]
    assert (await _add(client, lead, leader_membership, west["id"])).status_code == 403

    # Someone with only branch authority can't hand out whole-church access.
    _, branch_admin = await _staff_in(client, lead, "limited.hat@example-church.org", east)
    members = (await client.get("/api/v1/team", headers=lead)).json()
    target = next(m for m in members if m["email"] == "guard.hat@example-church.org" and m["unit_scope"] == east["id"])
    attempt = await client.post(
        f"/api/v1/team/{target['id']}/assignments",
        json={"role_id": SYSTEM_ROLE_STAFF_ID, "unit_scope": "all"},
        headers=auth(branch_admin["access_token"]),
    )
    assert attempt.status_code in (403, 404)


async def test_suspending_one_assignment_leaves_the_other_usable(client, church_payload):
    _, _, lead = await _church(client, church_payload, "asg4")
    east, west = await _branch(client, lead, "East"), await _branch(client, lead, "West")
    east_id, session = await _staff_in(client, lead, "pause.hat@example-church.org", east)
    west_id = (await _add(client, lead, east_id, west["id"])).json()["id"]

    # Suspend West; switching to it is refused, East carries on.
    assert (
        await client.patch(f"/api/v1/team/{west_id}", json={"status": "suspended"}, headers=lead)
    ).status_code == 200
    assert (await _switch(client, session, west_id)).status_code == 404
    assert (await client.get("/api/v1/members", headers=auth(session["access_token"]))).status_code == 200

    # Suspending the one a session is working under ends that session, not the person's other assignment.
    assert (await client.patch(f"/api/v1/team/{west_id}", json={"status": "active"}, headers=lead)).status_code == 200
    west_session = (await _switch(client, session, west_id)).json()
    assert (
        await client.patch(f"/api/v1/team/{west_id}", json={"status": "suspended"}, headers=lead)
    ).status_code == 200
    assert (await client.get("/api/v1/members", headers=auth(west_session["access_token"]))).status_code == 401
    refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": west_session["refresh_token"]})
    assert refreshed.status_code == 200
    assert refreshed.json()["active_membership"]["id"] == east_id  # falls back to what they still hold


async def test_inviting_someone_who_already_has_that_exact_role_is_a_conflict(client, church_payload):
    _, _, lead = await _church(client, church_payload, "asg5")
    east = await _branch(client, lead, "East")
    await _staff_in(client, lead, "twice.hat@example-church.org", east)
    again = await client.post(
        "/api/v1/team/invite",
        json={"email": "twice.hat@example-church.org", "role_id": SYSTEM_ROLE_STAFF_ID, "unit_scope": east["id"]},
        headers=lead,
    )
    assert again.status_code == 409
