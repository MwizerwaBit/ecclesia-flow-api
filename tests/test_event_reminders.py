"""Event reminders, a day and an hour before (TODO.md, Notifications module N7).

`send_due_event_reminders(now)` is run with an explicit clock, so each test says
exactly how long before the gathering it is. Recipients read their own bells
through `GET /notifications`. The job is global (it looks at every church), so
each assertion is about this test's own church.
"""

import uuid
from datetime import UTC, datetime, timedelta

from dateutil import tz as dateutil_tz

from app.core.database import platform_session
from app.modules.activity import reminders
from tests.test_notifications_send import _church, _joined
from tests.test_notifications_triggers import _inbox

BASE = "/api/v1"
ZONE_NAME = "Africa/Kigali"  # UTC+2, no daylight saving: local time = UTC + 2 h
ZONE = dateutil_tz.gettz(ZONE_NAME)


def _start() -> datetime:
    """A gathering start about ten days out, on a whole minute."""
    return (datetime.now(UTC) + timedelta(days=10)).replace(minute=0, second=0, microsecond=0)


async def _set_zone(tenant_id: str) -> None:
    async with platform_session() as db:
        await db.execute_raw("update organizations set timezone = $2 where id = $1::uuid", tenant_id, ZONE_NAME)


async def _group(client, lead, members: dict[str, str]) -> str:
    """A group with these members (id -> role), all made active."""
    group = (await client.post(f"{BASE}/groups", json={"name": f"Choir {uuid.uuid4().hex[:4]}"}, headers=lead)).json()
    for member_id, role in members.items():
        resp = await client.post(
            f"{BASE}/groups/{group['id']}/members", json={"member_ids": [member_id], "role": role}, headers=lead
        )
        assert resp.status_code in (200, 201), resp.text
    async with platform_session() as db:
        await db.execute_raw("update group_memberships set status = 'active' where group_id = $1::uuid", group["id"])
    return group["id"]


async def _event(client, lead, *, start, title="Choir practice", group_id=None, rule=None, all_day=False,
                 visibility=None, publish=True) -> dict:  # fmt: skip
    body = {"title": title, "type": "meeting", "start_date_time": start.isoformat()}
    if group_id:
        body["group_id"] = group_id
    if rule:
        body["recurrence_rule"] = rule
    if all_day:
        body["is_all_day"] = True
    if visibility:
        body["visibility"] = visibility
    created = await client.post(f"{BASE}/events", json=body, headers=lead)
    assert created.status_code == 200, created.text
    event = created.json()
    if publish:
        assert (await client.post(f"{BASE}/events/{event['id']}/publish", headers=lead)).status_code == 200
    return event


async def _register(tenant_id: str, event_id: str, email: str) -> None:
    """Someone signed up for the event. Seeded straight into the Forms tables because the
    public link refuses drafts and private gatherings, and the reminders must ignore those too."""
    async with platform_session() as db:
        await db.execute_raw(
            "insert into forms (tenant_id, owner_kind, owner_id, title, status, public_key)"
            " values ($1::uuid, 'event', $2::uuid, 'Registration', 'published',"
            " replace(gen_random_uuid()::text, '-', ''))"
            " on conflict (tenant_id, owner_kind, owner_id) where owner_kind is not null do nothing",
            tenant_id,
            event_id,
        )
        await db.execute_raw(
            "insert into form_submissions (tenant_id, form_id, respondent_name, respondent_email, form_snapshot)"
            ' select $1::uuid, id, \'Someone\', $3, \'{"form_title": "Registration", "fields": []}\'::jsonb'
            " from forms where owner_kind = 'event' and owner_id = $2::uuid",
            tenant_id,
            event_id,
            email,
        )


async def _reminders(client, headers) -> list[dict]:
    return [n for n in await _inbox(client, headers) if n["type"] == "reminder"]


def _titles(items) -> list[str]:
    return [n["title"] for n in items]


async def _setup(client, church_payload, label):
    """A church with: Ann (group member), Bea (group leader), Cleo (not in the group, registers
    with her account email), Dora (unrelated), and the leader as author."""
    leader, lead, slug = await _church(client, church_payload, label)
    tenant = leader["active_membership"]["tenant_id"]
    await _set_zone(tenant)
    ann, ann_h, ann_m = await _joined(client, slug, leader, "Ann")
    bea, bea_h, bea_m = await _joined(client, slug, leader, "Bea")
    cleo, cleo_h, _ = await _joined(client, slug, leader, "Cleo")
    dora, dora_h, _ = await _joined(client, slug, leader, "Dora")
    group_id = await _group(client, lead, {ann_m: "member", bea_m: "leader"})
    emails = {}
    async with platform_session() as db:
        for key, session in (("cleo", cleo),):
            emails[key] = (await db.query_raw("select email from users where id = $1::uuid", session["user"]["id"]))[0][
                "email"
            ]
    return {
        "tenant": tenant,
        "leader": leader,
        "lead": lead,
        "group": group_id,
        "ann": ann_h,
        "bea": bea_h,
        "cleo": cleo_h,
        "dora": dora_h,
        "cleo_email": emails["cleo"],
    }


# ───────────────────────────── who is reminded, and when ─────────────────────────────


async def test_the_day_before_reminder_reaches_the_group_the_author_and_registrants_once(client, church_payload):
    c = await _setup(client, church_payload, "n7-day")
    start = _start()
    event = await _event(client, c["lead"], start=start, group_id=c["group"])
    await _register(c["tenant"], event["id"], c["cleo_email"])
    now = start - timedelta(hours=20)

    created = await reminders.send_due_event_reminders(now)
    assert created >= 4  # the global job may also have other churches' reminders

    local = start.astimezone(ZONE)
    for who in ("ann", "bea", "cleo", "lead"):  # group member, group leader, registrant, author
        (note,) = await _reminders(client, c[who])
        assert note["title"] == "Coming up: Choir practice"
        assert note["body"] == f'"Choir practice" starts {local:%a %d %b, %H:%M}.'  # in the church's time zone
        assert note["action_url"] == "/portal/events"
        assert (note["resource_type"], note["resource_id"]) == ("event", event["id"])
    assert await _reminders(client, c["dora"]) == []  # unrelated to the gathering

    # Running it again, even from a fresh process, tells nobody twice.
    reminders._handled.clear()
    await reminders.send_due_event_reminders(now)
    for who in ("ann", "bea", "cleo", "lead"):
        assert len(await _reminders(client, c[who])) == 1


async def test_the_hour_before_reminder_is_a_second_one_and_only_once(client, church_payload):
    c = await _setup(client, church_payload, "n7-hour")
    start = _start()
    await _event(client, c["lead"], start=start, group_id=c["group"])

    await reminders.send_due_event_reminders(start - timedelta(hours=20))
    await reminders.send_due_event_reminders(start - timedelta(minutes=30))
    reminders._handled.clear()
    await reminders.send_due_event_reminders(start - timedelta(minutes=10))  # still the hour stage: same key

    assert sorted(_titles(await _reminders(client, c["ann"]))) == [
        "Coming up: Choir practice",
        "Starting soon: Choir practice",
    ]


async def test_nothing_before_the_day_window_or_after_the_start(client, church_payload):
    c = await _setup(client, church_payload, "n7-window")
    start = _start()
    await _event(client, c["lead"], start=start, group_id=c["group"])
    await reminders.send_due_event_reminders(start - timedelta(hours=25))  # too early
    await reminders.send_due_event_reminders(start + timedelta(minutes=1))  # already started
    assert await _reminders(client, c["ann"]) == []


async def test_a_gathering_published_at_short_notice_gets_one_useful_reminder(client, church_payload):
    c = await _setup(client, church_payload, "n7-late")
    start = _start()
    await _event(client, c["lead"], start=start, group_id=c["group"])
    await reminders.send_due_event_reminders(start - timedelta(minutes=30))  # nothing earlier was ever sent
    assert _titles(await _reminders(client, c["ann"])) == ["Starting soon: Choir practice"]


async def test_drafts_canceled_and_private_gatherings(client, church_payload):
    c = await _setup(client, church_payload, "n7-states")
    start = _start()
    draft = await _event(client, c["lead"], start=start, title="Draft one", group_id=c["group"], publish=False)
    canceled = await _event(client, c["lead"], start=start, title="Canceled one", group_id=c["group"])
    private = await _event(
        client, c["lead"], start=start, title="Private one", group_id=c["group"], visibility="private"
    )
    await _register(c["tenant"], private["id"], c["cleo_email"])
    await _register(c["tenant"], draft["id"], c["cleo_email"])

    await reminders.send_due_event_reminders(start - timedelta(hours=20))
    assert (await client.post(f"{BASE}/events/{canceled['id']}/cancel", headers=c["lead"])).status_code == 200
    await reminders.send_due_event_reminders(start - timedelta(minutes=30))

    # Group members: the private one only, once at each stage. The draft is never announced, and the
    # canceled one stops after the day-before reminder.
    assert sorted(_titles(await _reminders(client, c["ann"]))) == [
        "Coming up: Canceled one",
        "Coming up: Private one",
        "Starting soon: Private one",
    ]
    # A registrant is not reminded of a private gathering, nor of a draft.
    assert [t for t in _titles(await _reminders(client, c["cleo"]))] == []
    assert draft["status"] == "draft"


async def test_a_repeating_gathering_is_reminded_for_each_occurrence(client, church_payload):
    c = await _setup(client, church_payload, "n7-repeat")
    start = _start()
    await _event(
        client, c["lead"], start=start - timedelta(days=14), group_id=c["group"], rule="FREQ=WEEKLY"
    )  # a Tuesday-style series whose dates include `start` and `start + 7 days`

    await reminders.send_due_event_reminders(start - timedelta(hours=20))
    assert len(await _reminders(client, c["ann"])) == 1
    await reminders.send_due_event_reminders(start - timedelta(hours=20))  # same occurrence: nothing new
    assert len(await _reminders(client, c["ann"])) == 1

    await reminders.send_due_event_reminders(start + timedelta(days=7) - timedelta(hours=20))  # next week's
    notes = await _reminders(client, c["ann"])
    assert len(notes) == 2
    assert notes[0]["body"] != notes[1]["body"]  # a different date each time


async def test_a_gathering_that_moves_gets_reminders_for_its_new_time(client, church_payload):
    c = await _setup(client, church_payload, "n7-moved")
    start = _start()
    event = await _event(client, c["lead"], start=start, group_id=c["group"])
    await reminders.send_due_event_reminders(start - timedelta(hours=20))
    async with platform_session() as db:
        await db.execute_raw(
            "update events set start_date_time = $2::timestamptz where id = $1::uuid",
            event["id"],
            (start + timedelta(hours=3)).isoformat(),
        )
    await reminders.send_due_event_reminders(start - timedelta(hours=20))
    bodies = [n["body"] for n in await _reminders(client, c["ann"])]
    assert len(bodies) == 2 and len(set(bodies)) == 2


async def test_an_all_day_gathering_only_gets_the_day_before_reminder(client, church_payload):
    c = await _setup(client, church_payload, "n7-allday")
    start = (datetime.now(UTC) + timedelta(days=10)).astimezone(ZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    await _event(client, c["lead"], start=start, group_id=c["group"], all_day=True, title="Retreat")

    await reminders.send_due_event_reminders(start - timedelta(minutes=30))  # inside the last hour: nothing for all-day
    assert await _reminders(client, c["ann"]) == []
    await reminders.send_due_event_reminders(start - timedelta(hours=20))
    (note,) = await _reminders(client, c["ann"])
    assert note["body"] == f'"Retreat" starts {start.astimezone(ZONE):%a %d %b}.'  # a date, no time


async def test_a_suspended_church_is_not_reminded(client, church_payload):
    c = await _setup(client, church_payload, "n7-suspended")
    start = _start()
    await _event(client, c["lead"], start=start, group_id=c["group"])
    async with platform_session() as db:
        await db.execute_raw("update organizations set status = 'suspended' where id = $1::uuid", c["tenant"])
    await reminders.send_due_event_reminders(start - timedelta(hours=20))
    async with platform_session() as db:
        left = await db.query_raw(
            "select count(*)::int as n from notifications where tenant_id = $1::uuid and type = 'reminder'", c["tenant"]
        )
    assert left[0]["n"] == 0


# ───────────────────────────── preferences and email ─────────────────────────────


async def test_reminders_follow_each_persons_choices(client, church_payload):
    c = await _setup(client, church_payload, "n7-prefs")
    start = _start()
    await _event(client, c["lead"], start=start, group_id=c["group"])
    # Ann turns reminders off in the app; Bea keeps the app but turns the email off.
    await client.put(
        f"{BASE}/notification-preferences", json={"preferences": {"reminder": {"in_app": False}}}, headers=c["ann"]
    )
    await client.put(
        f"{BASE}/notification-preferences", json={"preferences": {"reminder": {"email": False}}}, headers=c["bea"]
    )
    await reminders.send_due_event_reminders(start - timedelta(hours=20))

    assert await _reminders(client, c["ann"]) == []
    assert len(await _reminders(client, c["bea"])) == 1
    async with platform_session() as db:
        rows = await db.query_raw(
            "select u.id as user_id, count(d.id)::int as emails from users u"
            " join notifications n on n.user_id = u.id and n.type = 'reminder' and n.tenant_id = $1::uuid"
            " left join notification_deliveries d on d.notification_id = n.id"
            " group by u.id",
            c["tenant"],
        )
    by_emails = sorted(r["emails"] for r in rows)
    assert by_emails.count(0) == 1  # Bea: the in-app reminder, no email
    assert by_emails.count(1) == len(by_emails) - 1  # everyone else who was reminded gets the email too


async def test_a_second_worker_cannot_double_send(client, church_payload):
    import asyncio

    c = await _setup(client, church_payload, "n7-parallel")
    start = _start()
    await _event(client, c["lead"], start=start, group_id=c["group"])
    reminders._handled.clear()
    await asyncio.gather(*[reminders.send_due_event_reminders(start - timedelta(hours=20)) for _ in range(3)])
    assert len(await _reminders(client, c["ann"])) == 1 and len(await _reminders(client, c["lead"])) == 1
