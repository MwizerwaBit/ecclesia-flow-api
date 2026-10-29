"""The notification service other modules call (TODO.md, Notifications module N3).

Run on a church's own session (`tenant_session`), as a caller does: recipient
checks, duplicate prevention, opt-outs, content rules, and that it is part of
the caller's transaction.
"""

import pytest

from app.core.database import platform_session, tenant_session
from app.modules.notifications.service import BODY_MAX, TITLE_MAX, notify
from tests.test_notifications_api import _church, _colleague

EVENT_ID = "7f1c2f0e-4444-4a2b-8c3d-000000000004"


async def _two_users(client, church_payload, label: str):
    """(tenant_id, leader_user_id, colleague_user_id) in one church, plus its leader's session."""
    _, session, lead, tenant = await _church(client, church_payload, label)
    colleague, _ = await _colleague(client, lead)
    return tenant, session["user"]["id"], colleague["user"]["id"]


async def _rows(tenant, user_id=None):
    async with platform_session() as db:
        return await db.query_raw(
            "select * from notifications where tenant_id = $1::uuid and ($2::uuid is null or user_id = $2::uuid)"
            " order by created_at, id",
            tenant,
            user_id,
        )


# ───────────────────────────── creating ─────────────────────────────


async def test_notifies_each_member_once_with_the_given_content(client, church_payload):
    tenant, leader, colleague = await _two_users(client, church_payload, "n3-basic")
    async with tenant_session(tenant, leader) as db:
        # The leader is listed twice and in upper case: still one notification.
        result = await notify(
            db,
            tenant,
            [leader, colleague, leader.upper()],
            "event",
            "Choir moved",
            "Choir now meets at 18:00.\nSee you there.",
            action_url="/staff/events/abc?tab=history",
            resource=("event", EVENT_ID),
            dedupe_key="event.moved:abc",
            actor_user_id=leader,
        )
    assert (result.created, result.duplicates, result.opted_out, result.skipped) == (2, 0, 0, 0)

    rows = await _rows(tenant)
    assert {r["user_id"] for r in rows} == {leader, colleague}
    row = rows[0]
    assert (row["type"], row["title"], row["is_read"], row["read_at"]) == ("event", "Choir moved", False, None)
    assert row["body"] == "Choir now meets at 18:00.\nSee you there."  # line breaks kept
    assert row["action_url"] == "/staff/events/abc?tab=history"
    assert (row["resource_type"], row["resource_id"], row["actor_user_id"]) == ("event", EVENT_ID, leader)
    assert row["dedupe_key"] == "event.moved:abc" and row["tenant_id"] == tenant


async def test_nobody_to_notify_is_a_quiet_zero(client, church_payload):
    tenant, *_ = await _two_users(client, church_payload, "n3-none")
    async with tenant_session(tenant) as db:
        result = await notify(db, tenant, [], "system", "Hello", "Body")
    assert (result.created, result.skipped) == (0, 0)
    assert await _rows(tenant) == []


# ───────────────────────────── duplicates ─────────────────────────────


async def test_the_same_trigger_does_not_notify_twice(client, church_payload):
    tenant, leader, colleague = await _two_users(client, church_payload, "n3-dupe")

    async def send(users, key):
        async with tenant_session(tenant, leader) as db:
            return await notify(db, tenant, users, "event", "Published", "It is on.", dedupe_key=key)

    first = await send([leader], "event.published:1")
    again = await send([leader], "event.published:1")  # a retried request, an overlapping job
    assert (first.created, again.created, again.duplicates) == (1, 0, 1)

    # A new recipient for the same trigger still gets theirs; the first is not repeated.
    widened = await send([leader, colleague], "event.published:1")
    assert (widened.created, widened.duplicates) == (1, 1)
    # A different trigger is a different notification.
    other = await send([leader], "event.published:2")
    assert other.created == 1
    assert len(await _rows(tenant, leader)) == 2 and len(await _rows(tenant, colleague)) == 1

    # Without a key nothing is deduplicated: it is up to the caller.
    await send([leader], None)
    await send([leader], None)
    assert len(await _rows(tenant, leader)) == 4


async def test_parallel_identical_triggers_create_one(client, church_payload):
    import asyncio

    tenant, leader, _ = await _two_users(client, church_payload, "n3-race")

    async def send():
        async with tenant_session(tenant, leader) as db:
            return await notify(db, tenant, [leader], "reminder", "Soon", "Starts in 1 hour.", dedupe_key="rem:1h")

    results = await asyncio.gather(*[send() for _ in range(6)])
    assert len(await _rows(tenant, leader)) == 1
    assert sum(r.created for r in results) == 1


# ───────────────────────────── who can be notified ─────────────────────────────


async def test_recipients_outside_the_church_or_inactive_are_skipped_and_counted(client, church_payload):
    tenant, leader, colleague = await _two_users(client, church_payload, "n3-who")
    other_tenant, other_leader, _ = await _two_users(client, church_payload, "n3-who-other")
    async with platform_session() as db:
        await db.execute_raw(
            "update tenant_memberships set status = 'suspended' where tenant_id = $1::uuid and user_id = $2::uuid",
            tenant,
            colleague,
        )
    ghost = "7f1c2f0e-5555-4a2b-8c3d-000000000005"

    async with tenant_session(tenant, leader) as db:
        result = await notify(
            db,
            tenant,
            [leader, colleague, other_leader, ghost, "not-an-id", ""],
            "system",
            "Hello",
            "Body",
        )
    # Only the leader: a suspended member, another church's user, an unknown id and two non-ids are skipped.
    assert (result.created, result.skipped) == (1, 5)
    assert {r["user_id"] for r in await _rows(tenant)} == {leader}
    assert await _rows(other_tenant) == []

    # A deactivated account is skipped too, even with an active membership.
    async with platform_session() as db:
        await db.execute_raw("update tenant_memberships set status = 'active' where user_id = $1::uuid", colleague)
        await db.execute_raw("update users set status = 'inactive' where id = $1::uuid", colleague)
    async with tenant_session(tenant, leader) as db:
        again = await notify(db, tenant, [colleague], "system", "Hello", "Body")
    assert (again.created, again.skipped) == (0, 1)


async def test_a_church_cannot_notify_another_churchs_user(client, church_payload):
    tenant, leader, _ = await _two_users(client, church_payload, "n3-cross-a")
    other_tenant, other_leader, _ = await _two_users(client, church_payload, "n3-cross-b")
    async with tenant_session(tenant, leader) as db:
        result = await notify(db, tenant, [other_leader], "system", "Hello", "Body")
    assert (result.created, result.skipped) == (0, 1)
    assert await _rows(other_tenant, other_leader) == []


# ───────────────────────────── preferences ─────────────────────────────


async def test_in_app_opt_out_is_respected_except_where_it_must_stay_on(client, church_payload):
    tenant, leader, colleague = await _two_users(client, church_payload, "n3-prefs")
    async with platform_session() as db:
        await db.execute_raw(
            "insert into notification_preferences (user_id, preferences) values ($1::uuid, $2::jsonb)",
            colleague,
            '{"reminder": {"in_app": false}, "approval": {"in_app": false}}',
        )
    async with tenant_session(tenant, leader) as db:
        reminder = await notify(db, tenant, [leader, colleague], "reminder", "Soon", "Starts soon.")
        approval = await notify(db, tenant, [leader, colleague], "approval", "Needs you", "Please approve.")
        event = await notify(db, tenant, [colleague], "event", "Changed", "Moved.")
    assert (reminder.created, reminder.opted_out) == (1, 1)  # the colleague turned reminders off
    assert (approval.created, approval.opted_out) == (2, 0)  # a stored "off" for approval is ignored
    assert (event.created, event.opted_out) == (1, 0)  # other types unaffected
    assert {r["type"] for r in await _rows(tenant, colleague)} == {"approval", "event"}


# ───────────────────────────── content rules ─────────────────────────────


async def test_text_is_cleaned_and_clipped(client, church_payload):
    tenant, leader, _ = await _two_users(client, church_payload, "n3-clip")
    async with tenant_session(tenant, leader) as db:
        await notify(db, tenant, [leader], "system", "  Spaced \n out\x00 title " + "t" * 400, "b" * 5000 + "\x07")
    row = (await _rows(tenant, leader))[0]
    assert (
        row["title"].startswith("Spaced out title ") and len(row["title"]) == TITLE_MAX and row["title"].endswith("…")
    )
    assert len(row["body"]) == BODY_MAX and "\x07" not in row["body"]
    assert "\x00" not in row["title"] and "\n" not in row["title"]


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/staff/events/1",
        "http://localhost/staff",
        "//evil.example/staff",
        "javascript:alert(1)",
        "/\\evil.example",
        "staff/events/1",
        "/staff/../portal",
        "/staff/./events",
        "/staff/../../etc/passwd/ x",
        "/staff/events/1 2",
        "/staff/events/1\n",
        "/platform/orgs",  # no notifications live there
        "/api/v1/members",
        "/staffer",
        "/staff/" + "a" * 300,
        "",
    ],
)
async def test_external_or_odd_links_are_refused(client, church_payload, url):
    tenant, leader, _ = await _two_users(client, church_payload, "n3-url")
    async with tenant_session(tenant, leader) as db:
        with pytest.raises(ValueError):
            await notify(db, tenant, [leader], "system", "Hello", "Body", action_url=url)
    assert await _rows(tenant) == []


@pytest.mark.parametrize(
    "url",
    ["/staff", "/staff/", "/portal/events/7f1c2f0e", "/board/finance?tab=a&x=1", "/staff/events/abc?tab=history"],
)
async def test_in_app_links_are_accepted(client, church_payload, url):
    tenant, leader, _ = await _two_users(client, church_payload, "n3-url-ok")
    async with tenant_session(tenant, leader) as db:
        assert (await notify(db, tenant, [leader], "system", "Hello", "Body", action_url=url)).created == 1


async def test_a_bad_call_raises_before_anything_is_written(client, church_payload):
    tenant, leader, _ = await _two_users(client, church_payload, "n3-bad")
    bad_calls = [
        dict(type_="schedule_conflict", title="T", body="B"),
        dict(type_="system", title="  ", body="B"),
        dict(type_="system", title="T", body="\n \x00"),
        dict(type_="system", title="T", body="B", resource=("Event", EVENT_ID)),
        dict(type_="system", title="T", body="B", resource=("event", "not-a-uuid")),
        dict(type_="system", title="T", body="B", dedupe_key=""),
        dict(type_="system", title="T", body="B", dedupe_key="k" * 201),
        dict(type_="system", title="T", body="B", actor_user_id="someone"),
    ]
    for call in bad_calls:
        async with tenant_session(tenant, leader) as db:
            with pytest.raises(ValueError):
                await notify(db, tenant, [leader], call.pop("type_"), call.pop("title"), call.pop("body"), **call)
    assert await _rows(tenant) == []


# ───────────────────────────── the caller's transaction ─────────────────────────────


async def test_a_rolled_back_action_leaves_no_notification(client, church_payload):
    tenant, leader, _ = await _two_users(client, church_payload, "n3-tx")

    class ActionFailed(Exception):
        pass

    with pytest.raises(ActionFailed):
        async with tenant_session(tenant, leader) as db:
            result = await notify(db, tenant, [leader], "event", "Published", "Done.", dedupe_key="event.published:x")
            assert result.created == 1  # visible inside the transaction ...
            raise ActionFailed

    assert await _rows(tenant) == []  # ... gone with it
    # And the dedupe key was not used up by the rolled-back attempt.
    async with tenant_session(tenant, leader) as db:
        assert (
            await notify(db, tenant, [leader], "event", "Published", "Done.", dedupe_key="event.published:x")
        ).created == 1
