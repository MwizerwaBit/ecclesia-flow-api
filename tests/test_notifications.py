"""Notifications data model (TODO.md, Notifications module N1).

Exercised directly at the database, as the Phase 3 RLS tests are: the
constraints, indexes and policies are what keep the later service and API
honest, so they are checked without going through either.

Covers: the type list agreeing with the CHECK constraint, duplicate prevention
(the partial unique index), read_at / resource / dedupe-key invariants, the
delivery table's own checks, same-church-and-recipient integrity between a
delivery and its notification, cascade, and tenant isolation under app_tenant.
"""

import re

import pytest
from prisma.errors import PrismaError

from app.core.database import platform_session, tenant_session
from app.modules.notifications.schemas import NOTIFICATION_TYPES
from app.modules.notifications.service import DEFAULT_PREFERENCES


async def _two_churches(client, church_payload):
    """(tenant_id, user_id) for two separate churches."""
    out = []
    for label in ("notif-a", "notif-b"):
        resp = await client.post("/api/v1/auth/register", json=church_payload(label))
        assert resp.status_code == 200, resp.text
        session = resp.json()
        out.append((session["active_membership"]["tenant_id"], session["user"]["id"]))
    return out


_INSERT = """
    insert into notifications
        (id, tenant_id, user_id, type, title, body, dedupe_key, resource_type, resource_id, is_read, read_at)
    values
        (gen_random_uuid(), $1::uuid, $2::uuid, $3, 'Title', 'Body', $4, $5, $6::uuid, $7::boolean,
         case when $8::boolean then now() end)
"""


async def _notify(
    db,
    tenant_id,
    user_id,
    *,
    type_="system",
    dedupe_key=None,
    resource_type=None,
    resource_id=None,
    is_read=False,
    read_at=False,
    on_conflict="",
):
    """Insert one notification; returns the new id, or None when ON CONFLICT skipped it."""
    rows = await db.query_raw(
        _INSERT + on_conflict + " returning id",
        tenant_id,
        user_id,
        type_,
        dedupe_key,
        resource_type,
        resource_id,
        is_read,
        read_at,
    )
    return rows[0]["id"] if rows else None


async def _deliver(db, tenant_id, notification_id, user_id, *, channel="email", status="pending", sent=False):
    await db.execute_raw(
        """
        insert into notification_deliveries (tenant_id, notification_id, user_id, channel, status, sent_at)
        values ($1::uuid, $2::uuid, $3::uuid, $4, $5, case when $6::boolean then now() end)
        """,
        tenant_id,
        notification_id,
        user_id,
        channel,
        status,
        sent,
    )


async def _refused(coro_factory):
    """The statement must fail at the database; each attempt gets its own transaction."""
    with pytest.raises(PrismaError):
        async with platform_session() as db:
            await coro_factory(db)


# ───────────────────────────── types and defaults ─────────────────────────────


async def test_type_list_matches_the_database_constraint(client):
    async with platform_session() as db:
        rows = await db.query_raw(
            "select pg_get_constraintdef(oid) as def from pg_constraint where conname = 'notifications_type_check'"
        )
    assert set(re.findall(r"'([a-z_]+)'", rows[0]["def"])) == set(NOTIFICATION_TYPES)
    assert set(DEFAULT_PREFERENCES) == set(NOTIFICATION_TYPES)


async def test_every_known_type_is_accepted_and_others_refused(client, church_payload):
    (tenant_id, user_id), _ = await _two_churches(client, church_payload)
    async with platform_session() as db:
        for t in NOTIFICATION_TYPES:
            assert await _notify(db, tenant_id, user_id, type_=t)
    await _refused(lambda db: _notify(db, tenant_id, user_id, type_="schedule_conflict"))


# ───────────────────────────── duplicate prevention ─────────────────────────────


async def test_the_same_trigger_cannot_notify_a_user_twice(client, church_payload):
    (tenant_id, user_id), (_, other_user) = await _two_churches(client, church_payload)
    async with platform_session() as db:
        first = await _notify(db, tenant_id, user_id, dedupe_key="event.published:abc")
        assert first

        # ON CONFLICT DO NOTHING (what the notification service will use) is a quiet no-op.
        again = await _notify(
            db,
            tenant_id,
            user_id,
            dedupe_key="event.published:abc",
            on_conflict=" on conflict (tenant_id, user_id, dedupe_key) where dedupe_key is not null do nothing",
        )
        assert again is None
        count = await db.query_raw(
            "select count(*)::int as n from notifications where tenant_id = $1::uuid and user_id = $2::uuid",
            tenant_id,
            user_id,
        )
        assert count[0]["n"] == 1

        # A different user, or a different key, is a different notification.
        assert await _notify(db, tenant_id, other_user, dedupe_key="event.published:abc")
        assert await _notify(db, tenant_id, user_id, dedupe_key="event.published:def")

        # Without a key nothing is deduplicated.
        assert await _notify(db, tenant_id, user_id)
        assert await _notify(db, tenant_id, user_id)

    # Without ON CONFLICT the database itself refuses the second row.
    await _refused(lambda db: _notify(db, tenant_id, user_id, dedupe_key="event.published:abc"))


async def test_dedupe_key_must_be_reasonable(client, church_payload):
    (tenant_id, user_id), _ = await _two_churches(client, church_payload)
    await _refused(lambda db: _notify(db, tenant_id, user_id, dedupe_key=""))
    await _refused(lambda db: _notify(db, tenant_id, user_id, dedupe_key="x" * 201))
    async with platform_session() as db:
        assert await _notify(db, tenant_id, user_id, dedupe_key="x" * 200)


# ───────────────────────────── row invariants ─────────────────────────────


async def test_read_at_only_on_a_read_row(client, church_payload):
    (tenant_id, user_id), _ = await _two_churches(client, church_payload)
    async with platform_session() as db:
        assert await _notify(db, tenant_id, user_id, is_read=True, read_at=True)
        assert await _notify(db, tenant_id, user_id, is_read=True)  # read before read_at existed
        assert await _notify(db, tenant_id, user_id)
    await _refused(lambda db: _notify(db, tenant_id, user_id, is_read=False, read_at=True))

    # Marking unread again must clear read_at too.
    async with platform_session() as db:
        nid = await _notify(db, tenant_id, user_id, is_read=True, read_at=True)
    await _refused(lambda db: db.execute_raw("update notifications set is_read = false where id = $1::uuid", nid))
    async with platform_session() as db:
        await db.execute_raw("update notifications set is_read = false, read_at = null where id = $1::uuid", nid)


async def test_resource_reference_is_both_or_neither(client, church_payload):
    (tenant_id, user_id), _ = await _two_churches(client, church_payload)
    some_id = "7f1c2f0e-1111-4a2b-8c3d-000000000001"
    async with platform_session() as db:
        assert await _notify(db, tenant_id, user_id, resource_type="event", resource_id=some_id)
        assert await _notify(db, tenant_id, user_id)
    await _refused(lambda db: _notify(db, tenant_id, user_id, resource_type="event"))
    await _refused(lambda db: _notify(db, tenant_id, user_id, resource_id=some_id))
    await _refused(lambda db: _notify(db, tenant_id, user_id, resource_type="", resource_id=some_id))


async def test_the_actor_must_be_a_real_user(client, church_payload):
    (tenant_id, user_id), _ = await _two_churches(client, church_payload)
    ghost = "7f1c2f0e-2222-4a2b-8c3d-000000000002"
    await _refused(
        lambda db: db.execute_raw(
            "insert into notifications (id, tenant_id, user_id, type, title, body, actor_user_id)"
            " values (gen_random_uuid(), $1::uuid, $2::uuid, 'system', 't', 'b', $3::uuid)",
            tenant_id,
            user_id,
            ghost,
        )
    )


# ───────────────────────────── deliveries ─────────────────────────────


async def test_a_delivery_belongs_to_one_notification_and_channel(client, church_payload):
    (tenant_id, user_id), _ = await _two_churches(client, church_payload)
    async with platform_session() as db:
        nid = await _notify(db, tenant_id, user_id)
        await _deliver(db, tenant_id, nid, user_id)
        row = (
            await db.query_raw(
                "select status, attempts from notification_deliveries where notification_id = $1::uuid", nid
            )
        )[0]
        assert row == {"status": "pending", "attempts": 0}
        # A second channel is a second delivery.
        await _deliver(db, tenant_id, nid, user_id, channel="sms")

    # A retry updates the row; it never adds another for the same channel.
    await _refused(lambda db: _deliver(db, tenant_id, nid, user_id))


async def test_delivery_values_are_checked(client, church_payload):
    (tenant_id, user_id), _ = await _two_churches(client, church_payload)
    async with platform_session() as db:
        nid = await _notify(db, tenant_id, user_id)
    await _refused(lambda db: _deliver(db, tenant_id, nid, user_id, channel="carrier_pigeon"))
    await _refused(lambda db: _deliver(db, tenant_id, nid, user_id, status="delivered"))
    # "sent" says when.
    await _refused(lambda db: _deliver(db, tenant_id, nid, user_id, status="sent"))
    await _refused(
        lambda db: db.execute_raw(
            "insert into notification_deliveries (tenant_id, notification_id, user_id, channel, last_error)"
            " values ($1::uuid, $2::uuid, $3::uuid, 'email', $4)",
            tenant_id,
            nid,
            user_id,
            "x" * 501,
        )
    )
    await _refused(
        lambda db: db.execute_raw(
            "insert into notification_deliveries (tenant_id, notification_id, user_id, channel, attempts)"
            " values ($1::uuid, $2::uuid, $3::uuid, 'email', -1)",
            tenant_id,
            nid,
            user_id,
        )
    )
    async with platform_session() as db:
        await _deliver(db, tenant_id, nid, user_id, status="sent", sent=True)


async def test_a_delivery_cannot_point_at_another_churchs_or_another_users_notification(client, church_payload):
    (tenant_a, user_a), (tenant_b, user_b) = await _two_churches(client, church_payload)
    async with platform_session() as db:
        nid = await _notify(db, tenant_a, user_a)

    # Right church, wrong recipient.
    await _refused(lambda db: _deliver(db, tenant_a, nid, user_b))
    # Right recipient, wrong church.
    await _refused(lambda db: _deliver(db, tenant_b, nid, user_a))


async def test_deleting_a_notification_removes_its_deliveries(client, church_payload):
    (tenant_id, user_id), _ = await _two_churches(client, church_payload)
    async with platform_session() as db:
        nid = await _notify(db, tenant_id, user_id)
        await _deliver(db, tenant_id, nid, user_id)
        await db.execute_raw("delete from notifications where id = $1::uuid", nid)
        left = await db.query_raw("select 1 from notification_deliveries where notification_id = $1::uuid", nid)
        assert left == []


# ───────────────────────────── tenant isolation (app_tenant) ─────────────────────────────


async def test_a_church_cannot_see_or_touch_another_churchs_notifications(client, church_payload):
    (tenant_a, user_a), (tenant_b, user_b) = await _two_churches(client, church_payload)
    async with platform_session() as db:
        b_note = await _notify(db, tenant_b, user_b, dedupe_key="k")
        await _deliver(db, tenant_b, b_note, user_b)
        a_note = await _notify(db, tenant_a, user_a)
        await _deliver(db, tenant_a, a_note, user_a)

    async with tenant_session(tenant_a, user_a) as tx:
        # Reads: only its own rows, even when the other church's id is asked for by name.
        mine = await tx.query_raw("select id from notifications")
        assert {r["id"] for r in mine} == {a_note}
        assert await tx.query_raw("select 1 from notifications where id = $1::uuid", b_note) == []
        assert (
            await tx.query_raw("select 1 from notification_deliveries where notification_id = $1::uuid", b_note) == []
        )
        assert len(await tx.query_raw("select 1 from notification_deliveries")) == 1
        # Writes: nothing of the other church's changes.
        assert await tx.execute_raw("update notifications set is_read = true where id = $1::uuid", b_note) == 0
        assert (
            await tx.execute_raw(
                "update notification_deliveries set attempts = 9 where notification_id = $1::uuid", b_note
            )
            == 0
        )

    # Inserting a row for the other church is refused by the policy.
    with pytest.raises(PrismaError):
        async with tenant_session(tenant_a, user_a) as tx:
            await _notify(tx, tenant_b, user_b)
    with pytest.raises(PrismaError):
        async with tenant_session(tenant_a, user_a) as tx:
            await _deliver(tx, tenant_b, b_note, user_b)

    # With no church chosen, nothing is visible at all.
    async with tenant_session(tenant_a) as tx:
        await tx.execute_raw("select set_config('app.tenant_id', '', true)")
        assert await tx.query_raw("select 1 from notifications") == []
        assert await tx.query_raw("select 1 from notification_deliveries") == []

    async with platform_session() as db:
        untouched = await db.query_raw("select is_read from notifications where id = $1::uuid", b_note)
        assert untouched == [{"is_read": False}]


async def test_a_church_connection_cannot_delete_delivery_history(client, church_payload):
    (tenant_id, user_id), _ = await _two_churches(client, church_payload)
    async with platform_session() as db:
        nid = await _notify(db, tenant_id, user_id)
        await _deliver(db, tenant_id, nid, user_id)

    with pytest.raises(PrismaError):
        async with tenant_session(tenant_id, user_id) as tx:
            await tx.execute_raw("delete from notification_deliveries where notification_id = $1::uuid", nid)

    # It can record an attempt, though: that is what sending does.
    async with tenant_session(tenant_id, user_id) as tx:
        changed = await tx.execute_raw(
            "update notification_deliveries set attempts = attempts + 1, last_error = 'timeout'"
            " where notification_id = $1::uuid",
            nid,
        )
        assert changed == 1
