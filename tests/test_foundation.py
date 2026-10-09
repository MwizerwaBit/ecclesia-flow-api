"""Organisational foundation — end to end against the dev DB.

Covers: hierarchy integrity (moves keep the closure table and therefore unit
scope correct; cycles and cross-church parents refused), configurable unit
types, unit-scoped Row-Level Security exercised directly at the database
(not through the API's own checks), unit memberships with history and
associate visibility, organisation affiliations and what a parent may see,
and terminology.
"""

import uuid

import pyotp
import pytest
from prisma.errors import PrismaError

from app.core.database import platform_session, tenant_client, tenant_session
from app.modules.rbac.models import SYSTEM_ROLE_STAFF_ID
from tests.conftest import accept_invite, adult_member, auth, unique_email


async def _church(client, church_payload, label: str):
    payload = church_payload(label)
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200, resp.text
    session = resp.json()
    h = auth(session["access_token"])
    root_id = (await client.get("/api/v1/hierarchy/units", headers=h)).json()[0]["id"]
    return payload, session, h, root_id


async def _mfa(client, payload: dict, session: dict) -> dict:
    """Turn on MFA for this leader and return an MFA-verified session."""
    h = auth(session["access_token"])
    setup = (await client.post("/api/v1/auth/mfa/setup", headers=h)).json()
    verify = await client.post("/api/v1/auth/mfa/verify", headers=h, json={"code": pyotp.TOTP(setup["secret"]).now()})
    assert verify.status_code == 204, verify.text
    login = await client.post("/api/v1/auth/login", json={"email": payload["email"], "password": payload["password"]})
    done = await client.post(
        "/api/v1/auth/mfa/challenge",
        json={"challenge_token": login.json()["challenge_token"], "code": setup["backup_codes"][0]},
    )
    assert done.status_code == 200, done.text
    return done.json()


async def _unit(client, h, name: str, parent_id: str, **extra) -> str:
    resp = await client.post(
        "/api/v1/hierarchy/units", json={"name": name, "type": "Branch", "parent_id": parent_id, **extra}, headers=h
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _member(client, h, first: str, unit_id: str) -> str:
    resp = await client.post("/api/v1/members", json=adult_member(first, unit_id=unit_id), headers=h)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _scoped_staff(client, h, unit_id: str) -> dict:
    invite = await client.post(
        "/api/v1/team/invite",
        json={"email": unique_email("scoped"), "role_id": SYSTEM_ROLE_STAFF_ID, "unit_scope": unit_id},
        headers=h,
    )
    assert invite.status_code == 200, invite.text
    accepted = await accept_invite(client, invite.json()["invite_url"])
    assert accepted["unit_scope_id"] == unit_id
    return auth(accepted["access_token"])


async def _member_ids(client, h) -> set[str]:
    resp = await client.get("/api/v1/members", headers=h)
    assert resp.status_code == 200, resp.text
    return {m["id"] for m in resp.json()}


# ───────────────────────────── Hierarchy integrity ─────────────────────────────


async def test_moving_a_branch_moves_its_scope_and_cycles_are_refused(client, church_payload):
    _, session, lead, root = await _church(client, church_payload, "move")
    east = await _unit(client, lead, "East", root)
    west = await _unit(client, lead, "West", root)
    cell = await _unit(client, lead, "East Cell", east)
    person = await _member(client, lead, "Celly", cell)

    east_staff = await _scoped_staff(client, lead, east)
    assert person in await _member_ids(client, east_staff)

    # Move the cell from East to West: East's staff lose it on the next request.
    moved = await client.patch(f"/api/v1/hierarchy/units/{cell}", json={"parent_id": west}, headers=lead)
    assert moved.status_code == 204, moved.text
    assert person not in await _member_ids(client, east_staff)
    assert (await client.get(f"/api/v1/members/{person}", headers=east_staff)).status_code == 404
    units = {u["id"]: u for u in (await client.get("/api/v1/hierarchy/units", headers=lead)).json()}
    assert units[cell]["parent_id"] == west and units[cell]["depth"] == 2

    # No unit can go under its own sub-unit.
    cycle = await client.patch(f"/api/v1/hierarchy/units/{west}", json={"parent_id": cell}, headers=lead)
    assert cycle.status_code == 409 and cycle.json()["error"]["code"] == "cycle"

    # Another church's unit is not a valid parent — through the API...
    _, _, other_lead, other_root = await _church(client, church_payload, "move-other")
    foreign = await client.patch(f"/api/v1/hierarchy/units/{east}", json={"parent_id": other_root}, headers=lead)
    assert foreign.status_code == 404
    # ...nor directly in the database, where the FK alone would have allowed it.
    with pytest.raises(PrismaError):
        async with tenant_session(session["active_membership"]["tenant_id"], session["user"]["id"]) as tx:
            await tx.execute_raw(
                "update hierarchy_units set parent_id = $1::uuid where id = $2::uuid", other_root, east
            )


# ───────────────────────────── Unit types ─────────────────────────────


async def test_unit_types_shape_the_structure(client, church_payload):
    _, _, lead, root = await _church(client, church_payload, "types")
    presets = {p["key"] for p in (await client.get("/api/v1/hierarchy/unit-type-presets", headers=lead)).json()}
    assert {"single_church", "diocesan", "multi_campus", "association"} <= presets

    applied = await client.post(
        "/api/v1/hierarchy/unit-types/apply-preset", json={"preset": "single_church"}, headers=lead
    )
    assert applied.status_code == 200, applied.text
    types = {t["key"]: t for t in applied.json()}
    assert set(types) == {"church", "branch", "cell"}
    again = await client.post("/api/v1/hierarchy/unit-types/apply-preset", json={"preset": "diocesan"}, headers=lead)
    assert again.status_code == 409

    # Once types exist, free-text types are refused.
    untyped = await client.post(
        "/api/v1/hierarchy/units", json={"name": "Loose", "type": "Whatever", "parent_id": root}, headers=lead
    )
    assert untyped.status_code == 400 and untyped.json()["error"]["code"] == "unit_type_required"

    typed_root = await client.patch(
        f"/api/v1/hierarchy/units/{root}", json={"unit_type_id": types["church"]["id"]}, headers=lead
    )
    assert typed_root.status_code == 204, typed_root.text
    branch = await client.post(
        "/api/v1/hierarchy/units",
        json={"name": "North", "unit_type_id": types["branch"]["id"], "parent_id": root},
        headers=lead,
    )
    assert branch.status_code == 200, branch.text
    assert branch.json()["type"] == "Branch" and branch.json()["unit_type_key"] == "branch"

    # A branch may only sit under a church.
    nested = await client.post(
        "/api/v1/hierarchy/units",
        json={"name": "Sub-branch", "unit_type_id": types["branch"]["id"], "parent_id": branch.json()["id"]},
        headers=lead,
    )
    assert nested.status_code == 400 and nested.json()["error"]["code"] == "invalid_parent"
    rootless = await client.post(
        "/api/v1/hierarchy/units", json={"name": "Floating", "unit_type_id": types["branch"]["id"]}, headers=lead
    )
    assert rootless.status_code == 400 and rootless.json()["error"]["code"] == "invalid_parent"

    # Renaming a type renames every unit of that type — wording, not behaviour.
    renamed = await client.patch(
        f"/api/v1/hierarchy/unit-types/{types['branch']['id']}",
        json={"label": "Campus", "plural_label": "Campuses"},
        headers=lead,
    )
    assert renamed.status_code == 200 and renamed.json()["key"] == "branch"
    units = {u["id"]: u for u in (await client.get("/api/v1/hierarchy/units", headers=lead)).json()}
    assert units[branch.json()["id"]]["type"] == "Campus"

    in_use = await client.delete(f"/api/v1/hierarchy/unit-types/{types['branch']['id']}", headers=lead)
    assert in_use.status_code == 409
    unknown_parent = await client.post(
        "/api/v1/hierarchy/unit-types",
        json={"key": "zone", "label": "Zone", "plural_label": "Zones", "allowed_parent_keys": ["nope"]},
        headers=lead,
    )
    assert unknown_parent.status_code == 400


# ───────────────────────────── RLS at the database ─────────────────────────────


async def test_database_enforces_unit_scope_without_the_application(client, church_payload):
    _, session, lead, root = await _church(client, church_payload, "rls")
    tenant_id, user_id = session["active_membership"]["tenant_id"], session["user"]["id"]
    east = await _unit(client, lead, "East", root)
    west = await _unit(client, lead, "West", root)
    east_person = await _member(client, lead, "Easty", east)
    west_person = await _member(client, lead, "Westy", west)
    note = await client.post(
        f"/api/v1/members/{west_person}/pastoral-notes", json={"content": "Visited", "is_private": False}, headers=lead
    )
    assert note.status_code == 200, note.text
    await client.patch(f"/api/v1/members/{west_person}", json={"envelope_number": "0042"}, headers=lead)

    async with tenant_session(tenant_id, user_id, unit_scope_id=east) as tx:
        visible = {r["id"] for r in await tx.query_raw("select id from members")}
        assert visible == {east_person}
        assert await tx.query_raw("select id from pastoral_notes") == []
        # Writes to people outside the scope touch nothing...
        assert await tx.execute_raw("update members set notes = 'x' where id = $1::uuid", west_person) == 0
        # ...and church-wide uniqueness is still answered correctly.
        rows = await tx.query_raw("select tenant_next_envelope_number() as n, tenant_envelope_taken('0042', null) as t")
        assert rows[0]["n"] >= 43 and rows[0]["t"] is True

    # A record can't be moved out of the session's scope.
    with pytest.raises(PrismaError):
        async with tenant_session(tenant_id, user_id, unit_scope_id=east) as tx:
            await tx.execute_raw("update members set unit_id = $1::uuid where id = $2::uuid", west, east_person)

    # Fail closed: a transaction that sets the tenant but forgets the unit
    # scope sees the church's structure but none of its people.
    async with tenant_client.tx() as tx:
        await tx.execute_raw("select set_config('app.tenant_id', $1, true)", tenant_id)
        assert await tx.query_raw("select id from members") == []
        assert len(await tx.query_raw("select id from hierarchy_units")) == 3

    async with tenant_session(tenant_id, user_id) as tx:
        assert {r["id"] for r in await tx.query_raw("select id from members")} == {east_person, west_person}


# ───────────────────────────── Unit memberships ─────────────────────────────


async def test_unit_memberships_keep_history_and_associates_extend_visibility(client, church_payload):
    _, _, lead, root = await _church(client, church_payload, "memberships")
    east = await _unit(client, lead, "East", root)
    west = await _unit(client, lead, "West", root)
    person = await _member(client, lead, "Ann", east)
    west_staff = await _scoped_staff(client, lead, west)

    history = (await client.get(f"/api/v1/members/{person}/unit-memberships", headers=lead)).json()
    assert [(m["unit_id"], m["kind"], m["status"]) for m in history] == [(east, "home", "active")]
    assert (await client.get(f"/api/v1/members/{person}", headers=west_staff)).status_code == 404

    # An associate membership in West lets West see her — but not edit her.
    added = await client.post(f"/api/v1/members/{person}/unit-memberships", json={"unit_id": west}, headers=lead)
    assert added.status_code == 200, added.text
    associate_id = added.json()["id"]
    assert (await client.get(f"/api/v1/members/{person}", headers=west_staff)).status_code == 200
    assert person in await _member_ids(client, west_staff)
    edit = await client.patch(f"/api/v1/members/{person}", json={"notes": "x"}, headers=west_staff)
    assert edit.status_code == 403 and edit.json()["error"]["code"] == "out_of_scope"
    steal = await client.post(f"/api/v1/members/{person}/move", json={"unit_id": west}, headers=west_staff)
    assert steal.status_code == 403
    # West sees only its own part of her history.
    west_view = (await client.get(f"/api/v1/members/{person}/unit-memberships", headers=west_staff)).json()
    assert [m["kind"] for m in west_view] == ["associate"]
    duplicate = await client.post(f"/api/v1/members/{person}/unit-memberships", json={"unit_id": west}, headers=lead)
    assert duplicate.status_code == 409

    # Suspending the associate membership withdraws West's view.
    assert (
        await client.post(f"/api/v1/unit-memberships/{associate_id}/suspend", json={}, headers=lead)
    ).status_code == 200
    assert (await client.get(f"/api/v1/members/{person}", headers=west_staff)).status_code == 404
    assert (
        await client.post(f"/api/v1/unit-memberships/{associate_id}/reactivate", json={}, headers=lead)
    ).status_code == 200

    # Moving her home keeps the history: old home transferred (with the
    # reason), the associate membership closed because it became home.
    moved = await client.post(
        f"/api/v1/members/{person}/move", json={"unit_id": west, "reason": "Moved house"}, headers=lead
    )
    assert moved.status_code == 200, moved.text
    rows = {(m["unit_id"], m["kind"], m["status"]): m for m in moved.json()}
    assert set(rows) == {(west, "home", "active"), (east, "home", "transferred"), (west, "associate", "ended")}
    assert rows[(east, "home", "transferred")]["end_reason"] == "Moved house"
    assert (
        await client.patch(f"/api/v1/members/{person}", json={"notes": "ok"}, headers=west_staff)
    ).status_code == 200

    # The ordinary edit path records history too (database trigger).
    back = await client.patch(f"/api/v1/members/{person}", json={"unit_id": east}, headers=lead)
    assert back.status_code == 200, back.text
    history = (await client.get(f"/api/v1/members/{person}/unit-memberships", headers=lead)).json()
    current = [m for m in history if m["status"] == "active"]
    assert [(m["unit_id"], m["kind"]) for m in current] == [(east, "home")]
    assert len(history) == 4

    home_id = current[0]["id"]
    ended = await client.post(f"/api/v1/unit-memberships/{home_id}/end", json={}, headers=lead)
    assert ended.status_code == 400 and ended.json()["error"]["code"] == "home_membership_ends_by_move"

    # A unit people have belonged to keeps its history and can't be deleted.
    assert (await client.patch(f"/api/v1/members/{person}", json={"unit_id": east}, headers=lead)).status_code == 200
    gone = await client.delete(f"/api/v1/hierarchy/units/{west}", headers=lead)
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "has_history"


# ───────────────────────────── Affiliations ─────────────────────────────


async def _verify(org_id: str) -> None:
    async with platform_session() as db:
        await db.execute_raw("update organizations set verification_status = 'verified' where id = $1::uuid", org_id)


async def _slug(client, h) -> str:
    return (await client.get("/api/v1/org/profile", headers=h)).json()["slug"]


async def test_affiliation_lifecycle_and_what_a_parent_can_see(client, church_payload):
    d_payload, d_session, d_plain, _ = await _church(client, church_payload, "diocese")
    p_payload, p_session, p_plain, p_root = await _church(client, church_payload, "parish")
    p_person = await _member(client, p_plain, "Paula", p_root)
    diocese_id = d_session["active_membership"]["tenant_id"]
    parish_id = p_session["active_membership"]["tenant_id"]
    d_slug, p_slug = await _slug(client, d_plain), await _slug(client, p_plain)

    propose = {"counterpart_slug": d_slug, "counterpart_role": "parent", "grants": ["aggregate_stats"]}
    no_mfa = await client.post("/api/v1/affiliations", json=propose, headers=p_plain)
    assert no_mfa.status_code == 403 and no_mfa.json()["error"]["code"] == "mfa_required"

    d = auth((await _mfa(client, d_payload, d_session))["access_token"])
    p = auth((await _mfa(client, p_payload, p_session))["access_token"])

    unverified = await client.post("/api/v1/affiliations", json=propose, headers=p)
    assert unverified.status_code == 400 and unverified.json()["error"]["code"] == "parent_not_verified"
    await _verify(diocese_id)

    requested = await client.post("/api/v1/affiliations", json={**propose, "message": "Please accept us"}, headers=p)
    assert requested.status_code == 200, requested.text
    aff = requested.json()
    assert aff["status"] == "requested" and aff["my_role"] == "child" and aff["initiated_by_me"] is True
    assert (await client.post(f"/api/v1/affiliations/{aff['id']}/accept", json={}, headers=p)).status_code == 403

    d_view = (await client.get("/api/v1/affiliations", headers=d)).json()
    assert [(a["id"], a["my_role"], a["initiated_by_me"]) for a in d_view] == [(aff["id"], "parent", False)]
    assert d_view[0]["counterpart"]["id"] == parish_id

    # The parent can't decide what the child shares.
    widen = await client.post(
        f"/api/v1/affiliations/{aff['id']}/accept", json={"grants": ["published_events"]}, headers=d
    )
    assert widen.status_code == 403 and widen.json()["error"]["code"] == "grants_are_childs"
    accepted = await client.post(f"/api/v1/affiliations/{aff['id']}/accept", json={"note": "Welcome"}, headers=d)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "active" and accepted.json()["grants"] == ["aggregate_stats"]

    summary = await client.get(f"/api/v1/affiliations/{aff['id']}/summary", headers=d)
    assert summary.status_code == 200, summary.text
    assert summary.json()["child_org_id"] == parish_id and summary.json()["units"] == 1
    assert summary.json()["active_members"] >= 1
    events = await client.get(f"/api/v1/affiliations/{aff['id']}/events", headers=d)
    assert events.status_code == 403 and events.json()["error"]["code"] == "not_shared"
    # The child can't read "its parent's" view of itself.
    assert (await client.get(f"/api/v1/affiliations/{aff['id']}/summary", headers=p)).status_code == 404

    # Affiliation is not access: the diocese still can't see the parish's people.
    assert p_person not in await _member_ids(client, d)
    async with tenant_session(diocese_id, d_session["user"]["id"]) as tx:
        assert await tx.query_raw("select id from members where tenant_id = $1::uuid", parish_id) == []
        assert await tx.query_raw("select id from hierarchy_units where tenant_id = $1::uuid", parish_id) == []

    # The child withdraws consent at any time.
    narrowed = await client.put(f"/api/v1/affiliations/{aff['id']}/grants", json={"grants": []}, headers=p)
    assert narrowed.status_code == 200 and narrowed.json()["grants"] == []
    assert (await client.get(f"/api/v1/affiliations/{aff['id']}/summary", headers=d)).status_code == 403
    assert (
        await client.put(f"/api/v1/affiliations/{aff['id']}/grants", json={"grants": []}, headers=d)
    ).status_code == 403

    # One parent at a time; no loops.
    await _verify(parish_id)
    second = await client.post("/api/v1/affiliations", json={**propose, "grants": []}, headers=p)
    assert second.status_code == 409 and second.json()["error"]["code"] == "has_parent"
    loop = await client.post(
        "/api/v1/affiliations", json={"counterpart_slug": p_slug, "counterpart_role": "parent"}, headers=d
    )
    assert loop.status_code == 409 and loop.json()["error"]["code"] == "cycle"

    # An organisation outside the relationship sees nothing of it.
    _, _, outsider, _ = await _church(client, church_payload, "outsider")
    outsider_view = await client.get("/api/v1/affiliations", headers=outsider)
    assert outsider_view.status_code == 200 and outsider_view.json() == []
    assert (await client.get(f"/api/v1/affiliations/{aff['id']}/summary", headers=outsider)).status_code == 404
    async with tenant_session(
        (await client.get("/api/v1/org/profile", headers=outsider)).json()["id"], str(uuid.uuid4())
    ) as tx:
        assert await tx.query_raw("select id from organization_affiliations") == []

    ended = await client.post(f"/api/v1/affiliations/{aff['id']}/end", json={"note": "Restructuring"}, headers=d)
    assert ended.status_code == 200 and ended.json()["status"] == "ended"
    assert (await client.get(f"/api/v1/affiliations/{aff['id']}/summary", headers=d)).status_code == 404
    again = await client.post("/api/v1/affiliations", json=propose, headers=p)
    assert again.status_code == 200 and again.json()["status"] == "requested"


# ───────────────────────────── Terminology ─────────────────────────────


async def test_terminology_is_configurable_wording_only(client, church_payload):
    payload, session, lead, _ = await _church(client, church_payload, "terms")
    default = (await client.get("/api/v1/org/terminology", headers=lead)).json()
    assert default["terms"]["unit"] == "Unit" and default["overrides"] == {}

    body = {
        "overrides": {"unit": "Parish", "units": "Parishes", "member": "Member", "administrator": " Parish  secretary "}
    }
    no_mfa = await client.put("/api/v1/org/terminology", json=body, headers=lead)
    assert no_mfa.status_code == 403 and no_mfa.json()["error"]["code"] == "mfa_required"

    mfa = auth((await _mfa(client, payload, session))["access_token"])
    saved = await client.put("/api/v1/org/terminology", json=body, headers=mfa)
    assert saved.status_code == 200, saved.text
    # Unchanged defaults aren't stored; whitespace is tidied.
    assert saved.json()["overrides"] == {"unit": "Parish", "units": "Parishes", "administrator": "Parish secretary"}
    assert (await client.get("/api/v1/org/terminology", headers=lead)).json()["terms"]["units"] == "Parishes"
    # Renaming "administrator" changed no permissions.
    perms = (await client.post("/api/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})).json()
    assert "org:settings" in perms["permissions"]

    unknown = await client.put("/api/v1/org/terminology", json={"overrides": {"bishop": "x"}}, headers=mfa)
    assert unknown.status_code == 422
