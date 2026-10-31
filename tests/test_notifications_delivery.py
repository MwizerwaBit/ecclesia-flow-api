"""Email delivery of notifications (TODO.md, Notifications module N4).

`notify` queues a pending delivery row in the caller's transaction; the worker
(`deliver_pending`) sends from committed rows. The mailer is replaced by a
recorder so the tests see exactly what would be sent and can make chosen
addresses fail. The worker is global (it takes any due row), so every assertion
is about this test's own rows and recipients.
"""

import asyncio
import smtplib

import pytest

from app.core.database import platform_session, tenant_session
from app.modules.notifications import delivery
from app.modules.notifications.service import notify
from tests.test_notifications_service import _rows, _two_users


class Recorder:
    def __init__(self) -> None:
        self.sent = []
        self.fail_for: set[str] = set()

    async def send(self, email) -> None:
        if email.to in self.fail_for:
            raise smtplib.SMTPRecipientsRefused({email.to: (550, b"mailbox unavailable")})
        self.sent.append(email)

    def to(self, address: str) -> list:
        return [m for m in self.sent if m.to == address]


@pytest.fixture
def mailer(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(delivery, "get_mailer", lambda: recorder)
    return recorder


async def _email_of(user_id: str) -> str:
    async with platform_session() as db:
        return (await db.query_raw("select email from users where id = $1::uuid", user_id))[0]["email"]


async def _deliveries(tenant: str, user_id: str | None = None) -> list[dict]:
    async with platform_session() as db:
        return await db.query_raw(
            "select d.* from notification_deliveries d where d.tenant_id = $1::uuid"
            " and ($2::uuid is null or d.user_id = $2::uuid) order by d.created_at, d.id",
            tenant,
            user_id,
        )


async def _send(tenant: str, actor: str, users: list[str], key: str | None = "k:1", type_="event", **extra):
    async with tenant_session(tenant, actor) as db:
        return await notify(
            db, tenant, users, type_, "Choir moved", "Choir now meets at 18:00.", dedupe_key=key, **extra
        )


async def _make_due(delivery_id: str) -> None:
    async with platform_session() as db:
        await db.execute_raw(
            "update notification_deliveries set next_attempt_at = now() - interval '1 second' where id = $1::uuid",
            delivery_id,
        )


# ───────────────────────────── queueing ─────────────────────────────


async def test_each_new_notification_queues_one_email_and_a_repeat_queues_none(client, church_payload):
    tenant, leader, colleague = await _two_users(client, church_payload, "n4-queue")
    first = await _send(tenant, leader, [leader, colleague])
    assert (first.created, first.emails_queued) == (2, 2)
    rows = await _deliveries(tenant)
    assert {(r["user_id"], r["channel"], r["status"], r["attempts"]) for r in rows} == {
        (leader, "email", "pending", 0),
        (colleague, "email", "pending", 0),
    }
    assert all(r["next_attempt_at"] and r["last_error"] is None and r["sent_at"] is None for r in rows)

    repeat = await _send(tenant, leader, [leader, colleague])  # the same trigger again
    assert (repeat.created, repeat.emails_queued) == (0, 0)
    assert len(await _deliveries(tenant)) == 2


async def test_email_can_be_left_to_the_caller(client, church_payload):
    """The in-app copy of a mail the caller sends itself (e.g. the renewal reminder) must not email twice."""
    tenant, leader, _ = await _two_users(client, church_payload, "n4-noemail")
    result = await _send(tenant, leader, [leader], email=False)
    assert (result.created, result.emails_queued) == (1, 0)
    assert await _deliveries(tenant) == [] and len(await _rows(tenant, leader)) == 1


async def test_email_follows_the_users_choice_per_type(client, church_payload):
    tenant, leader, colleague = await _two_users(client, church_payload, "n4-prefs")
    async with platform_session() as db:
        await db.execute_raw(
            "insert into notification_preferences (user_id, preferences) values ($1::uuid, $2::jsonb)",
            colleague,
            '{"event": {"email": false}, "reminder": {"in_app": false}}',
        )
    event = await _send(tenant, leader, [leader, colleague], key="e:1", type_="event")
    reminder = await _send(tenant, leader, [leader, colleague], key="r:1", type_="reminder")
    # Email off: still notified in-app, no email. In-app off: no notification, so no email either.
    assert (event.created, event.emails_queued) == (2, 1)
    assert (reminder.created, reminder.emails_queued) == (1, 1)
    assert {r["user_id"] for r in await _deliveries(tenant)} == {leader}
    assert len(await _rows(tenant, colleague)) == 1


async def test_a_rolled_back_action_queues_no_email(client, church_payload):
    tenant, leader, _ = await _two_users(client, church_payload, "n4-tx")
    with pytest.raises(RuntimeError):
        async with tenant_session(tenant, leader) as db:
            assert (await notify(db, tenant, [leader], "event", "T", "B", dedupe_key="x")).emails_queued == 1
            raise RuntimeError("the action failed")
    assert await _deliveries(tenant) == [] and await _rows(tenant) == []


# ───────────────────────────── sending ─────────────────────────────


async def test_the_worker_sends_a_plain_email_with_a_link_back_and_records_it(client, church_payload, mailer):
    tenant, leader, colleague = await _two_users(client, church_payload, "n4-send")
    await _send(tenant, leader, [colleague], action_url="/staff/events/abc?tab=history")
    address = await _email_of(colleague)

    stats = await delivery.deliver_pending(limit=500)
    assert stats.sent >= 1

    (mail,) = mailer.to(address)
    assert mail.subject.endswith(": Choir moved") and mail.subject.split(":")[0].startswith("Test Church")
    assert "Choir now meets at 18:00." in mail.body
    assert "Open it: http://localhost:5173/staff/events/abc?tab=history" in mail.body
    assert "http://localhost:5173/notifications/preferences" in mail.body
    (row,) = await _deliveries(tenant, colleague)
    assert (row["status"], row["attempts"], row["last_error"], row["next_attempt_at"]) == ("sent", 1, None, None)
    assert row["sent_at"]

    # A second pass has nothing to do for it.
    await delivery.deliver_pending(limit=500)
    assert len(mailer.to(address)) == 1
    # The in-app notification is untouched by sending.
    assert [r["is_read"] for r in await _rows(tenant, colleague)] == [False]


async def test_no_link_line_without_an_action_url(client, church_payload, mailer):
    tenant, leader, colleague = await _two_users(client, church_payload, "n4-nolink")
    await _send(tenant, leader, [colleague])
    await delivery.deliver_pending(limit=500)
    (mail,) = mailer.to(await _email_of(colleague))
    assert "Open it:" not in mail.body


async def test_parallel_workers_never_send_the_same_email_twice(client, church_payload, mailer):
    tenant, leader, colleague = await _two_users(client, church_payload, "n4-parallel")
    await _send(tenant, leader, [colleague])
    await asyncio.gather(*[delivery.deliver_pending(limit=500) for _ in range(4)])
    assert len(mailer.to(await _email_of(colleague))) == 1
    assert (await _deliveries(tenant, colleague))[0]["status"] == "sent"


# ───────────────────────────── failure, retry, giving up ─────────────────────────────


async def test_a_failed_send_is_recorded_scrubbed_and_retried_later(client, church_payload, mailer):
    tenant, leader, colleague = await _two_users(client, church_payload, "n4-fail")
    await _send(tenant, leader, [colleague])
    address = await _email_of(colleague)
    mailer.fail_for.add(address)

    stats = await delivery.deliver_pending(limit=500)
    assert stats.retrying >= 1
    (row,) = await _deliveries(tenant, colleague)
    assert (row["status"], row["attempts"]) == ("pending", 1)
    assert "SMTPRecipientsRefused" in row["last_error"] and "<address>" in row["last_error"]
    assert address not in row["last_error"] and "@" not in row["last_error"]
    assert row["next_attempt_at"] > row["updated_at"]  # not due yet

    # The notification itself is there and unaffected.
    assert len(await _rows(tenant, colleague)) == 1

    # Not due: an immediate pass leaves it alone.
    await delivery.deliver_pending(limit=500)
    assert (await _deliveries(tenant, colleague))[0]["attempts"] == 1

    # Once due, and the provider is back, it goes out and the error is cleared.
    mailer.fail_for.clear()
    await _make_due(row["id"])
    await delivery.deliver_pending(limit=500)
    (row,) = await _deliveries(tenant, colleague)
    assert (row["status"], row["attempts"], row["last_error"]) == ("sent", 2, None)
    assert len(mailer.to(address)) == 1


async def test_it_gives_up_after_the_maximum_attempts(client, church_payload, mailer):
    tenant, leader, colleague = await _two_users(client, church_payload, "n4-giveup")
    await _send(tenant, leader, [colleague])
    mailer.fail_for.add(await _email_of(colleague))

    for attempt in range(1, delivery.MAX_ATTEMPTS + 1):
        (row,) = await _deliveries(tenant, colleague)
        if attempt > 1:
            await _make_due(row["id"])
        await delivery.deliver_pending(limit=500)
    (row,) = await _deliveries(tenant, colleague)
    assert (row["status"], row["attempts"], row["next_attempt_at"]) == ("failed", delivery.MAX_ATTEMPTS, None)
    assert row["last_error"]

    # Done for good: no further attempts, even if it were due.
    await delivery.deliver_pending(limit=500)
    assert (await _deliveries(tenant, colleague))[0]["attempts"] == delivery.MAX_ATTEMPTS


async def test_one_bad_recipient_does_not_hold_up_the_others(client, church_payload, mailer):
    tenant, leader, colleague = await _two_users(client, church_payload, "n4-isolated")
    await _send(tenant, leader, [leader, colleague])
    mailer.fail_for.add(await _email_of(colleague))
    await delivery.deliver_pending(limit=500)
    by_user = {r["user_id"]: r["status"] for r in await _deliveries(tenant)}
    assert by_user == {leader: "sent", colleague: "pending"}


async def test_an_abandoned_claim_comes_back_after_its_lease(client, church_payload, mailer):
    tenant, leader, colleague = await _two_users(client, church_payload, "n4-lease")
    await _send(tenant, leader, [colleague])
    from app.modules.notifications import repository

    async with platform_session() as db:  # a worker claims it and dies before sending
        claimed = await repository.claim_due_email_deliveries(db, 500, delivery.LEASE_SECONDS)
    assert any(str(c["user_id"]) == colleague for c in claimed)
    await delivery.deliver_pending(limit=500)
    assert mailer.to(await _email_of(colleague)) == []  # leased: not taken by another worker

    (row,) = await _deliveries(tenant, colleague)
    await _make_due(row["id"])  # the lease runs out
    await delivery.deliver_pending(limit=500)
    assert len(mailer.to(await _email_of(colleague))) == 1


# ───────────────────────────── skipped at send time ─────────────────────────────


async def test_email_switched_off_after_queueing_is_not_sent(client, church_payload, mailer):
    tenant, leader, colleague = await _two_users(client, church_payload, "n4-late-optout")
    await _send(tenant, leader, [colleague], type_="event")
    async with platform_session() as db:
        await db.execute_raw(
            "insert into notification_preferences (user_id, preferences) values ($1::uuid, $2::jsonb)",
            colleague,
            '{"event": {"email": false}}',
        )
    await delivery.deliver_pending(limit=500)
    (row,) = await _deliveries(tenant, colleague)
    assert (row["status"], row["last_error"]) == ("skipped", "recipient turned email off")
    assert mailer.to(await _email_of(colleague)) == []


async def test_someone_who_left_is_not_emailed(client, church_payload, mailer):
    tenant, leader, colleague = await _two_users(client, church_payload, "n4-left")
    await _send(tenant, leader, [colleague])
    async with platform_session() as db:
        await db.execute_raw(
            "update tenant_memberships set status = 'suspended' where tenant_id = $1::uuid and user_id = $2::uuid",
            tenant,
            colleague,
        )
    await delivery.deliver_pending(limit=500)
    (row,) = await _deliveries(tenant, colleague)
    assert row["status"] == "skipped" and mailer.to(await _email_of(colleague)) == []


# ───────────────────────────── helpers ─────────────────────────────


def test_scrub_error_removes_addresses_and_caps_length():
    error = delivery.scrub_error(RuntimeError("550 5.1.1 <jane.doe+x@example-church.org>: user unknown\nbad"))
    assert error == "RuntimeError: 550 5.1.1 <<address>>: user unknown bad"
    assert "@" not in error
    assert len(delivery.scrub_error(RuntimeError("x" * 2000))) == 500


def test_email_is_built_from_the_notification_only():
    mail = delivery.build_email(
        {
            "email": "a@example.org",
            "display_name": "St Mark",
            "title": "Hello",
            "body": "Body text",
            "action_url": None,
            "type": "event",
        }
    )
    assert (mail.to, mail.subject) == ("a@example.org", "St Mark: Hello")
    assert mail.body.startswith("Body text\n\n") and "St Mark" in mail.body
