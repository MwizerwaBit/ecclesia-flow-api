"""Notifications API (TODO.md, Notifications module N2) — end to end.

Covers: the list (newest first, stable pages, filters, headers), the unread
count, one notification, read/unread, mark-all-read, preferences, and the
rules around them: recipient-only access (another user's notification is 404,
in the same church or another), the permission, concurrent updates, and that
the routes keep working for a suspended church.
"""

import asyncio

from app.core.database import platform_session
from app.modules.notifications.schemas import NOTIFICATION_TYPES
from app.modules.rbac.models import SYSTEM_ROLE_STAFF_ID
from tests.conftest import accept_invite, auth, unique_email
from tests.test_foundation import _mfa
from tests.test_notifications import _notify


async def _church(client, church_payload, label: str):
    payload = church_payload(label)
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200, resp.text
    session = resp.json()
    return payload, session, auth(session["access_token"]), session["active_membership"]["tenant_id"]


async def _colleague(client, lead, role_id: str = SYSTEM_ROLE_STAFF_ID):
    """A second signed-in user in the same church."""
    invite = await client.post(
        "/api/v1/team/invite", json={"email": unique_email("colleague"), "role_id": role_id}, headers=lead
    )
    assert invite.status_code == 200, invite.text
    session = await accept_invite(client, invite.json()["invite_url"], first="Col", last="League")
    return session, auth(session["access_token"])


async def _seed(db, tenant_id, user_id, count: int, *, type_="system", same_instant=False) -> list[str]:
    """`count` notifications titled N0 (newest) .. N{count-1}, one minute apart
    (or all at one instant, to exercise the tie-break). Returns ids, newest first."""
    ids = []
    for n in range(count):
        rows = await db.query_raw(
            """
            insert into notifications (id, tenant_id, user_id, type, title, body, created_at)
            values (gen_random_uuid(), $1::uuid, $2::uuid, $3, $4, 'Body',
                    case when $5::boolean then date_trunc('second', now())
                         else now() - ($6::int * interval '1 minute') end)
            returning id
            """,
            tenant_id,
            user_id,
            type_,
            f"N{n}",
            same_instant,
            n,
        )
        ids.append(rows[0]["id"])
    return ids


# ───────────────────────────── list ─────────────────────────────


async def test_list_is_newest_first_paged_in_sql_with_headers(client, church_payload):
    _, session, h, tenant = await _church(client, church_payload, "n2-list")
    async with platform_session() as db:
        await _seed(db, tenant, session["user"]["id"], 5)

    first = await client.get("/api/v1/notifications", params={"limit": 2}, headers=h)
    assert first.status_code == 200, first.text
    assert [n["title"] for n in first.json()] == ["N0", "N1"]
    assert first.headers["X-Total-Count"] == "5"
    assert first.headers["X-Page-Limit"] == "2" and first.headers["X-Page-Offset"] == "0"
    assert 'rel="next"' in first.headers["Link"] and 'rel="prev"' not in first.headers["Link"]

    last = await client.get("/api/v1/notifications", params={"limit": 2, "offset": 4}, headers=h)
    assert [n["title"] for n in last.json()] == ["N4"]
    assert last.headers["X-Total-Count"] == "5" and 'rel="prev"' in last.headers["Link"]

    beyond = await client.get("/api/v1/notifications", params={"offset": 50}, headers=h)
    assert beyond.json() == [] and beyond.headers["X-Total-Count"] == "5"

    assert (await client.get("/api/v1/notifications", params={"limit": 501}, headers=h)).status_code == 422
    assert (await client.get("/api/v1/notifications", params={"limit": 0}, headers=h)).status_code == 422


async def test_pages_never_overlap_or_skip_when_created_at_ties(client, church_payload):
    _, session, h, tenant = await _church(client, church_payload, "n2-ties")
    async with platform_session() as db:
        ids = await _seed(db, tenant, session["user"]["id"], 7, same_instant=True)

    seen = []
    for offset in range(0, 7, 2):
        page = await client.get("/api/v1/notifications", params={"limit": 2, "offset": offset}, headers=h)
        seen += [n["id"] for n in page.json()]
    assert sorted(seen) == sorted(ids) and len(seen) == len(set(seen)) == 7


async def test_empty_list_and_filters(client, church_payload):
    _, session, h, tenant = await _church(client, church_payload, "n2-filter")
    user = session["user"]["id"]
    empty = await client.get("/api/v1/notifications", headers=h)
    assert empty.status_code == 200 and empty.json() == [] and empty.headers["X-Total-Count"] == "0"

    async with platform_session() as db:
        events = await _seed(db, tenant, user, 2, type_="event")
        await _seed(db, tenant, user, 1, type_="billing")
    await client.patch(f"/api/v1/notifications/{events[0]}", json={"is_read": True}, headers=h)

    by_type = await client.get("/api/v1/notifications", params={"type": "event"}, headers=h)
    assert len(by_type.json()) == 2 and by_type.headers["X-Total-Count"] == "2"
    unread = await client.get("/api/v1/notifications", params={"status": "unread"}, headers=h)
    assert len(unread.json()) == 2 and all(not n["is_read"] for n in unread.json())
    both = await client.get("/api/v1/notifications", params={"status": "unread", "type": "event"}, headers=h)
    assert [n["id"] for n in both.json()] == [events[1]]

    assert (
        await client.get("/api/v1/notifications", params={"type": "schedule_conflict"}, headers=h)
    ).status_code == 422
    assert (await client.get("/api/v1/notifications", params={"status": "read"}, headers=h)).status_code == 422


async def test_list_carries_only_what_the_screen_needs(client, church_payload):
    _, session, h, tenant = await _church(client, church_payload, "n2-shape")
    async with platform_session() as db:
        await _notify(
            db,
            tenant,
            session["user"]["id"],
            dedupe_key="secret-trigger-key",
            resource_type="event",
            resource_id="7f1c2f0e-3333-4a2b-8c3d-000000000003",
        )
    item = (await client.get("/api/v1/notifications", headers=h)).json()[0]
    assert set(item) == {
        "id", "user_id", "type", "title", "body", "is_read", "read_at", "action_url",
        "resource_type", "resource_id", "created_at",
    }  # fmt: skip
    assert "secret-trigger-key" not in str(item)


# ───────────────────────────── unread count, detail, read/unread ─────────────────────────────


async def test_unread_count_follows_read_state_and_is_per_user(client, church_payload):
    _, session, h, tenant = await _church(client, church_payload, "n2-count")
    colleague, ch = await _colleague(client, h)
    async with platform_session() as db:
        mine = await _seed(db, tenant, session["user"]["id"], 3)
        await _seed(db, tenant, colleague["user"]["id"], 4)

    async def count(headers):
        resp = await client.get("/api/v1/notifications/unread-count", headers=headers)
        assert resp.status_code == 200, resp.text
        return resp.json()["count"]

    assert await count(h) == 3 and await count(ch) == 4
    await client.patch(f"/api/v1/notifications/{mine[0]}", json={"is_read": True}, headers=h)
    assert await count(h) == 2 and await count(ch) == 4
    await client.patch(f"/api/v1/notifications/{mine[0]}", json={"is_read": False}, headers=h)
    assert await count(h) == 3


async def test_read_and_unread_set_and_clear_the_read_time(client, church_payload):
    _, session, h, tenant = await _church(client, church_payload, "n2-read")
    async with platform_session() as db:
        (nid,) = await _seed(db, tenant, session["user"]["id"], 1)
    url = f"/api/v1/notifications/{nid}"

    assert (await client.get(url, headers=h)).json()["read_at"] is None
    read = await client.patch(url, json={"is_read": True}, headers=h)
    assert read.status_code == 200 and read.json()["is_read"] is True and read.json()["read_at"]

    # Reading it again changes nothing, including the time it was first read.
    again = await client.patch(url, json={"is_read": True}, headers=h)
    assert again.json()["read_at"] == read.json()["read_at"]

    unread = await client.patch(url, json={"is_read": False}, headers=h)
    assert unread.json()["is_read"] is False and unread.json()["read_at"] is None

    # No body at all still means "read" (the call the old route took).
    assert (await client.patch(url, headers=h)).json()["is_read"] is True
    assert (await client.patch(url, json={"is_read": "yes"}, headers=h)).status_code == 422


async def test_concurrent_updates_settle_on_one_consistent_state(client, church_payload):
    _, session, h, tenant = await _church(client, church_payload, "n2-race")
    async with platform_session() as db:
        (nid,) = await _seed(db, tenant, session["user"]["id"], 1)
    calls = [client.patch(f"/api/v1/notifications/{nid}", json={"is_read": True}, headers=h) for _ in range(8)]
    calls += [client.post("/api/v1/notifications/mark-all-read", headers=h) for _ in range(4)]
    results = await asyncio.gather(*calls)
    assert all(r.status_code in (200, 204) for r in results)
    final = (await client.get(f"/api/v1/notifications/{nid}", headers=h)).json()
    assert final["is_read"] is True and final["read_at"]
    assert (await client.get("/api/v1/notifications/unread-count", headers=h)).json() == {"count": 0}


async def test_mark_all_read_touches_only_my_unread_notifications(client, church_payload):
    _, session, h, tenant = await _church(client, church_payload, "n2-all")
    _, other_session, other_h, other_tenant = await _church(client, church_payload, "n2-all-other")
    colleague, ch = await _colleague(client, h)
    async with platform_session() as db:
        await _seed(db, tenant, session["user"]["id"], 3)
        await _seed(db, tenant, colleague["user"]["id"], 2)
        await _seed(db, other_tenant, other_session["user"]["id"], 2)

    assert (await client.post("/api/v1/notifications/mark-all-read", headers=h)).status_code == 204
    assert (await client.get("/api/v1/notifications/unread-count", headers=h)).json() == {"count": 0}
    assert (await client.get("/api/v1/notifications/unread-count", headers=ch)).json() == {"count": 2}
    assert (await client.get("/api/v1/notifications/unread-count", headers=other_h)).json() == {"count": 2}

    # Doing it again is harmless.
    assert (await client.post("/api/v1/notifications/mark-all-read", headers=h)).status_code == 204
    assert len((await client.get("/api/v1/notifications", headers=h)).json()) == 3


# ───────────────────────────── recipient-only access ─────────────────────────────


async def test_nobody_reads_or_changes_someone_elses_notification(client, church_payload):
    _, session, h, tenant = await _church(client, church_payload, "n2-owner")
    _, intruder_session, intruder_h, _ = await _church(client, church_payload, "n2-intruder")
    colleague, ch = await _colleague(client, h)
    async with platform_session() as db:
        (nid,) = await _seed(db, tenant, session["user"]["id"], 1)
    url = f"/api/v1/notifications/{nid}"

    # A colleague in the same church, and a user of another church: both get 404, never 403.
    for who in (ch, intruder_h):
        assert (await client.get(url, headers=who)).status_code == 404
        assert (await client.patch(url, json={"is_read": True}, headers=who)).status_code == 404
        assert (await client.get("/api/v1/notifications", headers=who)).json() == []

    async with platform_session() as db:
        row = await db.query_raw("select is_read from notifications where id = $1::uuid", nid)
        assert row == [{"is_read": False}]

    # Unknown and malformed ids.
    assert (
        await client.get("/api/v1/notifications/7f1c2f0e-9999-4a2b-8c3d-000000000009", headers=h)
    ).status_code == 404
    assert (await client.get("/api/v1/notifications/not-a-uuid", headers=h)).status_code == 422
    assert (await client.get(url, headers=h)).status_code == 200


async def test_routes_need_notifications_read_and_a_session(client, church_payload):
    payload, session, h, _ = await _church(client, church_payload, "n2-perm")
    mfa = auth((await _mfa(client, payload, session))["access_token"])
    role = await client.post("/api/v1/roles", json={"name": "No bell", "permissions": ["members:read"]}, headers=mfa)
    assert role.status_code == 200, role.text
    _, nobell = await _colleague(client, h, role_id=role.json()["id"])

    nid = "7f1c2f0e-8888-4a2b-8c3d-000000000008"
    calls = [
        client.get("/api/v1/notifications", headers=nobell),
        client.get("/api/v1/notifications/unread-count", headers=nobell),
        client.get(f"/api/v1/notifications/{nid}", headers=nobell),
        client.patch(f"/api/v1/notifications/{nid}", json={"is_read": True}, headers=nobell),
        client.post("/api/v1/notifications/mark-all-read", headers=nobell),
        client.get("/api/v1/notification-preferences", headers=nobell),
        client.put("/api/v1/notification-preferences", json={"preferences": {}}, headers=nobell),
    ]
    assert [r.status_code for r in await asyncio.gather(*calls)] == [403] * 7

    assert (await client.get("/api/v1/notifications")).status_code == 401
    assert (await client.get("/api/v1/notifications", headers=h)).status_code == 200


async def test_a_suspended_church_still_reaches_its_notifications(client, church_payload):
    _, session, h, tenant = await _church(client, church_payload, "n2-suspended")
    async with platform_session() as db:
        await _seed(db, tenant, session["user"]["id"], 1)
        await db.execute_raw("update organizations set status = 'suspended' where id = $1::uuid", tenant)

    # Business data is closed off ...
    assert (await client.get("/api/v1/members", headers=h)).status_code != 200
    # ... but "your payment was rejected" must still arrive.
    listed = await client.get("/api/v1/notifications", headers=h)
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert (await client.get("/api/v1/notifications/unread-count", headers=h)).json() == {"count": 1}
    assert (await client.post("/api/v1/notifications/mark-all-read", headers=h)).status_code == 204


# ───────────────────────────── preferences ─────────────────────────────


async def test_preferences_default_merge_and_validate(client, church_payload):
    _, session, h, _ = await _church(client, church_payload, "n2-prefs")
    colleague, ch = await _colleague(client, h)

    defaults = (await client.get("/api/v1/notification-preferences", headers=h)).json()
    assert defaults["user_id"] == session["user"]["id"]
    assert set(defaults["preferences"]) == set(NOTIFICATION_TYPES)
    assert defaults["preferences"]["event"] == {"email": True, "push": True, "in_app": True, "sms": False}

    # Only what is sent changes; the rest keeps its default.
    saved = await client.put(
        "/api/v1/notification-preferences", json={"preferences": {"event": {"email": False, "sms": True}}}, headers=h
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["preferences"]["event"] == {"email": False, "push": True, "in_app": True, "sms": True}
    assert saved.json()["preferences"]["system"] == defaults["preferences"]["system"]
    again = await client.put(
        "/api/v1/notification-preferences", json={"preferences": {"reminder": {"in_app": False}}}, headers=h
    )
    assert again.json()["preferences"]["event"]["email"] is False  # earlier choice kept
    assert again.json()["preferences"]["reminder"]["in_app"] is False

    # They are mine alone.
    assert (await client.get("/api/v1/notification-preferences", headers=ch)).json()["preferences"]["event"][
        "email"
    ] is True

    bad = [
        {"preferences": {"carrier_pigeon": {"email": True}}},
        {"preferences": {"event": {"telegram": True}}},
        {"preferences": {"event": {"email": "yes"}}},
        {"preferences": {"event": {"email": 1}}},
        {"preferences": {"event": True}},
        {"preferences": {"approval": {"in_app": False}}},
        {"preferences": {"billing": {"in_app": False}}},
        {"preferences": []},
        {},
    ]
    for body in bad:
        resp = await client.put("/api/v1/notification-preferences", json=body, headers=h)
        assert resp.status_code == 422, (body, resp.text)
    # Switching email off for an always-in-app type is fine.
    ok = await client.put(
        "/api/v1/notification-preferences", json={"preferences": {"approval": {"email": False}}}, headers=h
    )
    assert ok.status_code == 200 and ok.json()["preferences"]["approval"]["in_app"] is True


async def test_stored_preferences_that_break_the_rules_are_corrected_on_read(client, church_payload):
    """Rows written before the rules (or by hand) never leak an unknown key or turn off a protected channel."""
    _, session, h, _ = await _church(client, church_payload, "n2-legacy")
    async with platform_session() as db:
        await db.execute_raw(
            "insert into notification_preferences (user_id, preferences) values ($1::uuid, $2::jsonb)",
            session["user"]["id"],
            '{"billing": {"in_app": false, "email": false, "fax": true}, "old_type": {"email": true}, "event": "x"}',
        )
    prefs = (await client.get("/api/v1/notification-preferences", headers=h)).json()["preferences"]
    assert prefs["billing"] == {"email": False, "push": True, "in_app": True, "sms": False}
    assert "old_type" not in prefs
    assert prefs["event"] == {"email": True, "push": True, "in_app": True, "sms": False}
