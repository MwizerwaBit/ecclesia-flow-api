"""Manual notifications from an admin (TODO.md, Notifications module N6).

A sender with `notifications:send` writes a message and picks an audience. The
tests check what recipients see in their own bells, the counts reported back, and
the guards around it: permission, branch scope, idempotency, the rate limit and the
audit entry (which never carries the message).
"""

import asyncio
import json
import uuid

from app.core.database import platform_session
from app.modules.rbac.models import SYSTEM_ROLE_STAFF_ID
from tests.conftest import accept_invite, adult_member, auth, unique_email
from tests.test_features import _joined_member
from tests.test_notifications_triggers import _inbox, _register

BASE = "/api/v1"


def _key() -> str:
    return uuid.uuid4().hex


def _national_id() -> str:
    return f"1{uuid.uuid4().int % 10**15:015d}"


async def _church(client, church_payload, label):
    leader, lead = await _register(client, church_payload, label)
    slug = (await client.get(f"{BASE}/org/profile", headers=lead)).json()["slug"]
    return leader, lead, slug


async def _joined(client, slug, leader, first, *, unit_id=None, status=None):
    """A member who has a login (they joined through the church page): (session, headers, member id)."""
    session, member = await _joined_member(client, slug, leader, first, _national_id())
    if unit_id or status:
        async with platform_session() as db:
            await db.execute_raw(
                "update members set unit_id = coalesce($2::uuid, unit_id), status = coalesce($3::text, status)"
                " where id = $1::uuid",
                member["id"],
                unit_id,
                status,
            )
    return session, auth(session["access_token"]), member["id"]


async def _no_login(client, lead, first):
    resp = await client.post(f"{BASE}/members", json=adult_member(first), headers=lead)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _send(client, headers, *, key=None, title="Service moved", body="Sunday service is at 9:00.", audience=None):
    return await client.post(
        f"{BASE}/notifications/send",
        json={"request_key": key or _key(), "title": title, "body": body, "audience": audience or {}},
        headers=headers,
    )


async def _unit(client, lead, name, parent_id):
    resp = await client.post(
        f"{BASE}/hierarchy/units", json={"name": name, "type": "Branch", "parent_id": parent_id}, headers=lead
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _manual(client, headers) -> list[dict]:
    return [n for n in await _inbox(client, headers) if n["type"] == "manual"]


# ───────────────────────────── preview ─────────────────────────────


async def test_the_preview_counts_members_and_logins_and_nothing_else(client, church_payload):
    leader, lead, slug = await _church(client, church_payload, "n6-estimate")
    await _joined(client, slug, leader, "Ann", status="active")
    await _joined(client, slug, leader, "Bea", status="visitor")
    await _no_login(client, lead, "Cleo")

    everyone = await client.post(f"{BASE}/notifications/send/audience-estimate", json={}, headers=lead)
    assert everyone.status_code == 200, everyone.text
    assert everyone.json() == {"members": 3, "with_login": 2, "without_login": 1}
    visitors = await client.post(
        f"{BASE}/notifications/send/audience-estimate", json={"statuses": ["visitor"]}, headers=lead
    )
    assert visitors.json() == {"members": 1, "with_login": 1, "without_login": 0}

    for bad in ({"statuses": ["inactive"]}, {"statuses": ["nonsense"]}, {"unit_ids": ["not-a-uuid"]}):
        assert (
            await client.post(f"{BASE}/notifications/send/audience-estimate", json=bad, headers=lead)
        ).status_code == 422


# ───────────────────────────── sending ─────────────────────────────


async def test_a_send_reaches_members_with_logins_and_reports_the_rest(client, church_payload):
    leader, lead, slug = await _church(client, church_payload, "n6-send")
    ann_session, ann_h, _ = await _joined(client, slug, leader, "Ann")
    bea_session, bea_h, _ = await _joined(client, slug, leader, "Bea")
    await _no_login(client, lead, "Cleo")

    resp = await _send(client, lead, title="Service moved", body="Sunday service is at 9:00.\nSee you there.")
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert (result["audience_size"], result["notified"], result["without_login"]) == (3, 2, 1)
    assert (result["opted_out"], result["skipped"], result["already_sent"]) == (0, 0, False)

    for headers in (ann_h, bea_h):
        (note,) = await _manual(client, headers)
        assert (note["title"], note["body"]) == ("Service moved", "Sunday service is at 9:00.\nSee you there.")
        assert note["is_read"] is False and note["action_url"] is None
        assert "actor_user_id" not in note  # who sent it is not part of what a recipient reads

    # The email copy follows the same rules as any other notification.
    async with platform_session() as db:
        rows = await db.query_raw(
            "select count(*)::int as n from notification_deliveries where user_id = any($1::uuid[])",
            [ann_session["user"]["id"], bea_session["user"]["id"]],
        )
    assert rows[0]["n"] == 2


async def test_recipients_can_turn_manual_notifications_off(client, church_payload):
    leader, lead, slug = await _church(client, church_payload, "n6-optout")
    ann_session, ann_h, _ = await _joined(client, slug, leader, "Ann")
    await _joined(client, slug, leader, "Bea")
    saved = await client.put(
        f"{BASE}/notification-preferences", json={"preferences": {"manual": {"in_app": False}}}, headers=ann_h
    )
    assert saved.status_code == 200
    result = (await _send(client, lead)).json()
    assert (result["notified"], result["opted_out"]) == (1, 1)
    assert await _manual(client, ann_h) == []


async def test_the_same_request_key_sends_once(client, church_payload):
    leader, lead, slug = await _church(client, church_payload, "n6-idem")
    _, ann_h, _ = await _joined(client, slug, leader, "Ann")

    key = _key()
    first = (await _send(client, lead, key=key)).json()
    again = await _send(client, lead, key=key, title="A different message", body="Even with other words.")
    assert again.status_code == 200 and again.json()["already_sent"] is True
    assert again.json()["id"] == first["id"] and again.json()["notified"] == first["notified"] == 1
    assert [n["title"] for n in await _manual(client, ann_h)] == ["Service moved"]

    # A new key is a new send.
    assert (await _send(client, lead, title="Second")).json()["already_sent"] is False
    assert len(await _manual(client, ann_h)) == 2

    # A burst with one key (a double click that raced) still notifies once.
    burst_key = _key()
    results = await asyncio.gather(*[_send(client, lead, key=burst_key, title="Burst") for _ in range(5)])
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert sum(1 for r in results if not r.json()["already_sent"]) == 1
    assert len({r.json()["id"] for r in results}) == 1
    assert [n["title"] for n in await _manual(client, ann_h)].count("Burst") == 1


async def test_a_sender_is_limited_to_ten_sends_an_hour(client, church_payload):
    leader, lead, slug = await _church(client, church_payload, "n6-rate")
    await _joined(client, slug, leader, "Ann")
    keys = [_key() for _ in range(10)]
    for key in keys:
        assert (await _send(client, lead, key=key)).status_code == 200
    blocked = await _send(client, lead)
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "send_limit"
    # Repeating an earlier send is not a new send, so it is not limited.
    assert (await _send(client, lead, key=keys[0])).json()["already_sent"] is True


async def test_a_bad_request_is_refused_and_does_not_use_up_its_key(client, church_payload):
    leader, lead, slug = await _church(client, church_payload, "n6-validate")
    for bad in (
        {"title": "   "},
        {"body": "   "},
        {"title": "t" * 151},
        {"body": "b" * 1001},
        {"key": "short"},
        {"key": "has spaces in it!"},
        {"audience": {"statuses": ["inactive"]}},
    ):
        key = bad.pop("key", None)
        resp = await _send(client, lead, key=key, **bad)
        assert resp.status_code == 422, (bad, resp.text)

    # Nobody matches: refused, and the key is free for when there is somebody.
    key = _key()
    nobody = await _send(client, lead, key=key)
    assert nobody.status_code == 422 and nobody.json()["error"]["code"] == "empty_audience"
    await _joined(client, slug, leader, "Ann")
    assert (await _send(client, lead, key=key)).json()["notified"] == 1


# ───────────────────────────── who may send, and to whom ─────────────────────────────


async def test_only_people_with_the_permission_can_send(client, church_payload):
    leader, lead, slug = await _church(client, church_payload, "n6-perm")
    member_session, member_h, _ = await _joined(client, slug, leader, "Ann")

    assert (await _send(client, member_h)).status_code == 403
    assert (
        await client.post(f"{BASE}/notifications/send/audience-estimate", json={}, headers=member_h)
    ).status_code == 403
    assert (await client.post(f"{BASE}/notifications/send", json={})).status_code == 401

    invite = await client.post(
        f"{BASE}/team/invite", json={"email": unique_email("staff"), "role_id": SYSTEM_ROLE_STAFF_ID}, headers=lead
    )
    staff = await accept_invite(client, invite.json()["invite_url"], first="Sam", last="Staff")
    assert (await _send(client, auth(staff["access_token"]))).status_code == 200  # staff may


async def test_a_branch_sender_reaches_only_their_branch(client, church_payload):
    leader, lead, slug = await _church(client, church_payload, "n6-scope")
    root = (await client.get(f"{BASE}/hierarchy/units", headers=lead)).json()[0]["id"]
    east, west = await _unit(client, lead, "East", root), await _unit(client, lead, "West", root)
    east_session, east_h, _ = await _joined(client, slug, leader, "Eve", unit_id=east)
    west_session, west_h, _ = await _joined(client, slug, leader, "Will", unit_id=west)
    loose_session, loose_h, _ = await _joined(client, slug, leader, "Lou")

    invite = await client.post(
        f"{BASE}/team/invite",
        json={"email": unique_email("eaststaff"), "role_id": SYSTEM_ROLE_STAFF_ID, "unit_scope": east},
        headers=lead,
    )
    staff = await accept_invite(client, invite.json()["invite_url"], first="East", last="Staff")
    east_staff = auth(staff["access_token"])

    # The preview and the send see the branch and nothing else.
    estimate = await client.post(f"{BASE}/notifications/send/audience-estimate", json={}, headers=east_staff)
    assert estimate.json() == {"members": 1, "with_login": 1, "without_login": 0}
    sent = (await _send(client, east_staff, title="East only")).json()
    assert (sent["audience_size"], sent["notified"]) == (1, 1)
    assert len(await _manual(client, east_h)) == 1
    assert await _manual(client, west_h) == [] and await _manual(client, loose_h) == []

    # Naming another branch is refused outright, not quietly empty.
    for call in (
        client.post(f"{BASE}/notifications/send/audience-estimate", json={"unit_ids": [west]}, headers=east_staff),
        _send(client, east_staff, audience={"unit_ids": [west]}),
    ):
        refused = await call
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "out_of_scope"
    assert await _manual(client, west_h) == []

    # Naming their own branch is fine; whole-church staff can reach everyone or pick a branch.
    assert (await _send(client, east_staff, audience={"unit_ids": [east]})).json()["notified"] == 1
    everyone = (await _send(client, lead, title="Everyone")).json()
    assert everyone["notified"] == 3
    only_west = (await _send(client, lead, title="West", audience={"unit_ids": [west]})).json()
    assert (only_west["audience_size"], only_west["notified"]) == (1, 1)
    assert [n["title"] for n in await _manual(client, west_h)] == ["West", "Everyone"]


async def test_a_church_cannot_reach_another_churchs_members(client, church_payload):
    leader, lead, slug = await _church(client, church_payload, "n6-iso-a")
    other_leader, other_lead, other_slug = await _church(client, church_payload, "n6-iso-b")
    _, other_member_h, _ = await _joined(client, other_slug, other_leader, "Olga")
    other_root = (await client.get(f"{BASE}/hierarchy/units", headers=other_lead)).json()[0]["id"]
    await _joined(client, slug, leader, "Ann")

    # Another church's unit matches nobody here, however it is named.
    resp = await _send(client, lead, audience={"unit_ids": [other_root]})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "empty_audience"
    # And a send to everyone here does not spill over.
    assert (await _send(client, lead)).json()["notified"] == 1
    assert await _manual(client, other_member_h) == []


async def test_a_suspended_church_cannot_send_but_still_reads(client, church_payload):
    leader, lead, slug = await _church(client, church_payload, "n6-suspended")
    _, ann_h, _ = await _joined(client, slug, leader, "Ann")
    assert (await _send(client, lead)).status_code == 200
    async with platform_session() as db:
        await db.execute_raw(
            "update organizations set status = 'suspended' where id = $1::uuid",
            leader["active_membership"]["tenant_id"],
        )
    assert (await _send(client, lead)).status_code != 200
    assert len(await _manual(client, ann_h)) == 1  # what was already sent can still be read


# ───────────────────────────── audit ─────────────────────────────


async def test_each_send_is_audited_with_counts_and_never_the_message(client, church_payload):
    leader, lead, slug = await _church(client, church_payload, "n6-audit")
    await _joined(client, slug, leader, "Ann", status="active")
    await _no_login(client, lead, "Cleo")
    first_key = _key()
    result = (
        await _send(
            client,
            lead,
            key=first_key,
            title="Confidential title 8431",
            body="Confidential body 9917",
            audience={"statuses": ["active"]},
        )
    ).json()
    await _send(client, lead, key=_key())

    async with platform_session() as db:
        rows = await db.query_raw(
            "select actor_user_id, resource_type, metadata from audit_logs"
            " where tenant_id = $1::uuid and action = 'notifications.broadcast_sent' and resource_id = $2::uuid",
            leader["active_membership"]["tenant_id"],
            result["id"],
        )
        every = await db.query_raw(
            "select count(*)::int as n from audit_logs"
            " where tenant_id = $1::uuid and action = 'notifications.broadcast_sent'",
            leader["active_membership"]["tenant_id"],
        )
    (entry,) = rows
    assert entry["actor_user_id"] == leader["user"]["id"] and entry["resource_type"] == "notification_broadcast"
    meta = entry["metadata"] if isinstance(entry["metadata"], dict) else json.loads(entry["metadata"])
    assert meta["statuses"] == ["active"] and meta["audience_size"] == 2 and meta["notified"] == 1
    assert meta["without_login"] == 1 and meta["unit_ids"] == []
    assert "8431" not in str(meta) and "9917" not in str(meta) and "Confidential" not in str(meta)
    assert every[0]["n"] == 2  # two different sends, two entries
    replay = await client.post(
        f"{BASE}/notifications/send",
        json={"request_key": first_key, "title": "x", "body": "y", "audience": {}},
        headers=lead,
    )
    assert replay.json()["already_sent"] is True
    async with platform_session() as db:
        after = await db.query_raw(
            "select count(*)::int as n from audit_logs where tenant_id = $1::uuid"
            " and action = 'notifications.broadcast_sent'",
            leader["active_membership"]["tenant_id"],
        )
    assert after[0]["n"] == 2  # a replay is not audited again
