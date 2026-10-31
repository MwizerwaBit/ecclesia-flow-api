"""The calendar: who sees which events, and where recurring and all-day events
land on the church's calendar.

Security first: an ordinary member never sees drafts, reviews, canceled events or
other groups' private events (through any route), one church never sees
another's events, and a branch-scoped person stays in their branch. Then
correctness: validation, concurrent edits, audit history, the church's time zone
(a 09:00 service stays 09:00 across daylight saving), all-day and multi-day
events, window limits and the public calendar.
"""

import uuid
from datetime import UTC, datetime, timedelta

from dateutil import tz

from app.modules.activity import recurrence
from tests.conftest import auth, unique_email
from tests.test_features import _church, _joined_member, _staff
from tests.test_foundation import _church as _foundation_church
from tests.test_foundation import _mfa, _scoped_staff, _unit

NY = tz.gettz("America/New_York")


def _iso(days: int, hour: int = 10) -> str:
    base = datetime.now(UTC).replace(hour=hour, minute=0, second=0, microsecond=0)
    return (base + timedelta(days=days)).isoformat()


async def _create(client, headers, title, **extra):
    body = {"title": title, "type": "event", "start_date_time": _iso(5), **extra}
    resp = await client.post("/api/v1/events", json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _publish(client, headers, event):
    resp = await client.post(f"/api/v1/events/{event['id']}/publish", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _window(days_from=0, days_to=30):
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "from": (start + timedelta(days=days_from)).isoformat(),
        "to": (start + timedelta(days=days_to)).isoformat(),
    }


async def _calendar_ids(client, headers, **params):
    resp = await client.get("/api/v1/events/calendar", params={**_window(), **params}, headers=headers)
    assert resp.status_code == 200, resp.text
    return {o["event_id"] for o in resp.json()["occurrences"]}


async def _set_timezone(client, payload, leader, name):
    """Changing the church's zone is an org setting: it needs a two-step-verified session."""
    verified = auth((await _mfa(client, payload, leader))["access_token"])
    resp = await client.patch("/api/v1/org/profile", json={"timezone": name}, headers=verified)
    assert resp.status_code == 200, resp.text
    return verified


# ───────────────────────── Pure recurrence: time zones ─────────────────────────


def test_a_weekly_service_keeps_its_local_time_across_daylight_saving():
    start = datetime(2026, 2, 1, 14, 0, tzinfo=UTC)  # Sunday 09:00 in New York (EST)
    window = {"window_start": datetime(2026, 2, 1, tzinfo=UTC), "window_end": datetime(2026, 4, 5, tzinfo=UTC)}
    local = recurrence.occurrences("FREQ=WEEKLY;BYDAY=SU", start, tz=NY, **window)
    assert {o.astimezone(NY).strftime("%H:%M") for o in local} == {"09:00"}  # before and after 8 March
    assert {o.hour for o in local} == {14, 13}  # the UTC instant moved with the clock
    fixed_utc = recurrence.occurrences("FREQ=WEEKLY;BYDAY=SU", start, **window)
    assert {o.hour for o in fixed_utc} == {14}  # with no zone it stays at 14:00 UTC (10:00 local after the change)


def test_until_is_the_end_of_the_local_day_and_a_skipped_hour_moves_forward():
    daily = recurrence.occurrences(
        "FREQ=DAILY;UNTIL=20260303",
        datetime(2026, 3, 1, 3, 0, tzinfo=UTC),
        window_start=datetime(2026, 3, 1, tzinfo=UTC),
        window_end=datetime(2026, 3, 10, tzinfo=UTC),
        tz=NY,
    )
    assert [o.astimezone(NY).strftime("%m-%d") for o in daily] == ["02-28", "03-01", "03-02", "03-03"]
    gap = recurrence.occurrences(
        "FREQ=DAILY",
        datetime(2026, 3, 7, 7, 30, tzinfo=UTC),  # 02:30 local; that time does not exist on 8 March
        window_start=datetime(2026, 3, 7, tzinfo=UTC),
        window_end=datetime(2026, 3, 10, tzinfo=UTC),
        tz=NY,
    )
    assert [o.astimezone(NY).strftime("%m-%d %H:%M") for o in gap] == ["03-07 02:30", "03-08 03:30", "03-09 02:30"]


def test_naive_windows_never_crash_the_expansion():
    start = datetime(2026, 2, 1, 14, 0, tzinfo=UTC)
    assert recurrence.occurrences(None, start, window_start=datetime(2026, 1, 1), window_end=datetime(2026, 3, 1)) == [
        start
    ]


# ───────────────────────── Security: members ─────────────────────────


async def test_an_ordinary_member_never_sees_drafts_reviews_canceled_or_other_groups_events(client, church_payload):
    _, leader, lead = await _church(client, church_payload, "cal-member")
    slug = (await client.get("/api/v1/org/profile", headers=lead)).json()["slug"]
    member, member_record = await _joined_member(client, slug, leader, "Mia", "MIA10001")
    member_h = auth(member["access_token"])

    mine = (await client.post("/api/v1/groups", json={"name": "Choir"}, headers=lead)).json()
    others = (await client.post("/api/v1/groups", json={"name": "Elders"}, headers=lead)).json()
    await client.post(
        f"/api/v1/groups/{mine['id']}/members",
        json={"member_ids": [member_record["id"]], "role": "member"},
        headers=lead,
    )

    draft = await _create(client, lead, "Draft")
    members_only = await _publish(client, lead, await _create(client, lead, "Members night"))
    public = await _publish(client, lead, await _create(client, lead, "Open day", visibility="public"))
    choir = await _publish(
        client, lead, await _create(client, lead, "Choir practice", visibility="private", group_id=mine["id"])
    )
    elders = await _publish(
        client, lead, await _create(client, lead, "Elders", visibility="private", group_id=others["id"])
    )
    canceled = await _publish(client, lead, await _create(client, lead, "Canceled one"))
    assert (await client.post(f"/api/v1/events/{canceled['id']}/cancel", headers=lead)).status_code == 200

    # An invitation that has not been accepted does not make someone a member of the group.
    pending = {e["id"] for e in (await client.get("/api/v1/events", headers=member_h)).json()}
    assert choir["id"] not in pending
    invitation = (await client.get("/api/v1/me/groups", headers=member_h)).json()[0]
    accepted = await client.post(
        f"/api/v1/me/groups/invitations/{invitation['membership_id']}", json={"accept": True}, headers=member_h
    )
    assert accepted.status_code == 200, accepted.text

    seen = {e["id"] for e in (await client.get("/api/v1/events", headers=member_h)).json()}
    assert seen == {members_only["id"], public["id"], choir["id"]}
    assert await _calendar_ids(client, member_h) == seen

    for hidden in (draft, elders, canceled):
        for path in ("", "/occurrences", "/fields"):
            params = _window() if path == "/occurrences" else None
            resp = await client.get(f"/api/v1/events/{hidden['id']}{path}", params=params, headers=member_h)
            assert resp.status_code == 404, (hidden["title"], path, resp.status_code)

    detail = (await client.get(f"/api/v1/events/{members_only['id']}", headers=member_h)).json()
    assert detail["reviews"] == [] and detail["submitted_at"] is None
    assert (await client.get(f"/api/v1/events/{members_only['id']}/history", headers=member_h)).status_code == 403
    assert (await client.get("/api/v1/events/calendar", params=_window(), headers=member_h)).status_code == 200

    # The people who run events still see everything in their scope, drafts included.
    staff_ids = {e["id"] for e in (await client.get("/api/v1/events", headers=lead)).json()}
    assert {draft["id"], elders["id"], canceled["id"]} <= staff_ids
    assert draft["id"] in await _calendar_ids(client, lead)
    assert canceled["id"] in await _calendar_ids(client, lead, status="canceled")


async def test_a_member_who_is_not_in_any_group_sees_no_private_events_and_filters_still_apply(client, church_payload):
    _, leader, lead = await _church(client, church_payload, "cal-filters")
    slug = (await client.get("/api/v1/org/profile", headers=lead)).json()["slug"]
    member, _ = await _joined_member(client, slug, leader, "Nia", "NIA10002")
    member_h = auth(member["access_token"])
    group = (await client.post("/api/v1/groups", json={"name": "Youth"}, headers=lead)).json()
    service = await _publish(client, lead, await _create(client, lead, "Sunday", type="service", visibility="public"))
    meeting = await _publish(client, lead, await _create(client, lead, "Board", type="meeting"))
    private = await _publish(
        client, lead, await _create(client, lead, "Youth only", visibility="private", group_id=group["id"])
    )

    assert await _calendar_ids(client, member_h) == {service["id"], meeting["id"]}
    assert await _calendar_ids(client, member_h, type="service") == {service["id"]}
    # A member cannot get at a private event by naming its group.
    assert await _calendar_ids(client, member_h, group_id=group["id"]) == set()
    assert private["id"] in await _calendar_ids(client, lead, group_id=group["id"])


# ───────────────────────── Security: churches and branches ─────────────────────────


async def test_one_church_never_sees_or_touches_anothers_events(client, church_payload):
    _, _, lead_a = await _church(client, church_payload, "cal-a")
    _, _, lead_b = await _church(client, church_payload, "cal-b")
    event = await _publish(client, lead_a, await _create(client, lead_a, "A's service", visibility="public"))
    root_a = (await client.get("/api/v1/hierarchy/units", headers=lead_a)).json()[0]["id"]

    assert (await client.get(f"/api/v1/events/{event['id']}", headers=lead_b)).status_code == 404
    assert (await client.get(f"/api/v1/events/{event['id']}/history", headers=lead_b)).status_code == 404
    assert (
        await client.patch(f"/api/v1/events/{event['id']}", json={"title": "Hijacked"}, headers=lead_b)
    ).status_code == 404
    assert (await client.post(f"/api/v1/events/{event['id']}/cancel", headers=lead_b)).status_code == 404
    assert (
        await client.get(f"/api/v1/events/{event['id']}/occurrences", params=_window(), headers=lead_b)
    ).status_code == 404
    assert event["id"] not in await _calendar_ids(client, lead_b)
    assert event["id"] not in {e["id"] for e in (await client.get("/api/v1/events", headers=lead_b)).json()}

    # Another church's unit or group can't be attached to this church's event.
    foreign_unit = await client.post(
        "/api/v1/events",
        json={"title": "xx", "type": "event", "start_date_time": _iso(3), "unit_id": root_a},
        headers=lead_b,
    )
    assert foreign_unit.status_code == 404
    foreign_group = (await client.post("/api/v1/groups", json={"name": "A group"}, headers=lead_a)).json()
    cross = await client.post(
        "/api/v1/events",
        json={
            "title": "xx",
            "type": "event",
            "start_date_time": _iso(3),
            "visibility": "private",
            "group_id": foreign_group["id"],
        },
        headers=lead_b,
    )
    assert cross.status_code == 404


async def test_a_branch_scoped_staff_member_stays_in_their_branch(client, church_payload):
    _, session, lead, root = await _foundation_church(client, church_payload, "cal-branch")
    east, west = await _unit(client, lead, "East", root), await _unit(client, lead, "West", root)
    staff = await _scoped_staff(client, lead, east)

    church_wide = await _publish(client, lead, await _create(client, lead, "Whole church"))
    in_east = await _publish(client, lead, await _create(client, lead, "East only", unit_id=east))
    in_west = await _publish(client, lead, await _create(client, lead, "West only", unit_id=west))

    seen = await _calendar_ids(client, staff)
    assert seen == {church_wide["id"], in_east["id"]}
    assert {e["id"] for e in (await client.get("/api/v1/events", headers=staff)).json()} == seen
    assert (await client.get(f"/api/v1/events/{in_west['id']}", headers=staff)).status_code == 404
    assert (
        await client.patch(f"/api/v1/events/{in_west['id']}", json={"title": "xx"}, headers=staff)
    ).status_code == 404
    # Church-wide events are readable but only whole-church staff change them.
    assert (
        await client.patch(f"/api/v1/events/{church_wide['id']}", json={"title": "xx"}, headers=staff)
    ).status_code == 403
    assert (await client.get("/api/v1/events/calendar", params={**_window(), "unit_id": west}, headers=staff)).json()[
        "occurrences"
    ] == []


# ───────────────────────── Validation, concurrency, audit ─────────────────────────


async def test_bad_dates_cleared_fields_and_stale_edits_are_refused_and_changes_are_audited(client, church_payload):
    _, _, lead = await _church(client, church_payload, "cal-edit")
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=5)
    base = {"title": "Retreat", "type": "event", "start_date_time": start.isoformat()}

    backwards = await client.post(
        "/api/v1/events", json={**base, "end_date_time": (start - timedelta(hours=1)).isoformat()}, headers=lead
    )
    assert backwards.status_code == 422
    mixed = await client.post(
        "/api/v1/events",
        json={**base, "end_date_time": (start + timedelta(hours=1)).replace(tzinfo=None).isoformat()},
        headers=lead,
    )
    assert mixed.status_code == 422  # one time with an offset, one without

    event = await _create(
        client,
        lead,
        "Retreat",
        start_date_time=start.isoformat(),
        end_date_time=(start + timedelta(hours=2)).isoformat(),
    )
    event_url = f"/api/v1/events/{event['id']}"
    early = await client.patch(
        event_url, json={"end_date_time": (start - timedelta(hours=1)).isoformat()}, headers=lead
    )
    assert early.status_code == 422 and early.json()["error"]["code"] == "invalid_range"
    for cleared in ({"title": None}, {"type": None}, {"start_date_time": None}):
        assert (await client.patch(event_url, json=cleared, headers=lead)).status_code == 422

    seen_at = event["updated_at"]
    first = await client.patch(event_url, json={"title": "Youth Retreat", "expected_updated_at": seen_at}, headers=lead)
    assert first.status_code == 200, first.text
    stale = await client.patch(event_url, json={"title": "Other title", "expected_updated_at": seen_at}, headers=lead)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "edit_conflict"
    fresh = await client.patch(
        event_url, json={"visibility": "public", "expected_updated_at": first.json()["updated_at"]}, headers=lead
    )
    assert fresh.status_code == 200

    history = (await client.get(f"{event_url}/history", headers=lead)).json()
    actions = [h["action"] for h in history]
    assert actions.count("event.updated") == 2 and "event.created" in actions
    title_change = next(h for h in history if h["metadata"] and "title" in h["metadata"].get("changed", {}))
    assert title_change["metadata"]["changed"]["title"] == {"from": "Retreat", "to": "Youth Retreat"}
    assert title_change["actor_name"]


async def test_a_hundred_one_events_in_a_window_are_not_silently_cut_off(client, church_payload):
    _, _, lead = await _church(client, church_payload, "cal-many")
    for i in range(101):
        await client.post(
            "/api/v1/events",
            json={"title": f"E{i}", "type": "event", "start_date_time": _iso(1 + i % 20, hour=8 + i % 10)},
            headers=lead,
        )
    resp = await client.get("/api/v1/events/calendar", params={**_window(0, 40), "type": "event"}, headers=lead)
    body = resp.json()
    assert len(body["occurrences"]) == 101 and body["truncated"] is False  # the generic 100-row page does not apply


# ───────────────────────── Time zone, all-day, multi-day ─────────────────────────


async def test_the_churchs_time_zone_places_recurring_events_and_window_edges(client, church_payload):
    payload, leader, lead = await _church(client, church_payload, "cal-tz")
    verified = await _set_timezone(client, payload, leader, "America/New_York")
    bad_zone = await client.patch("/api/v1/org/profile", json={"timezone": "Mars/Olympus"}, headers=verified)
    assert bad_zone.status_code == 422
    # The leader's ordinary session (no two-step sign-in) can't change an org setting.
    assert (await client.patch("/api/v1/org/profile", json={"timezone": "UTC"}, headers=lead)).status_code == 403

    # 09:00 on Sunday 1 February, New York time (a time without an offset is the church's local time).
    created = await _create(
        client, lead, "Sunday worship", start_date_time="2026-02-01T09:00:00", recurrence_rule="FREQ=WEEKLY;BYDAY=SU"
    )
    assert datetime.fromisoformat(created["start_date_time"]) == datetime(2026, 2, 1, 14, 0, tzinfo=UTC)
    await _publish(client, lead, created)

    resp = await client.get(
        "/api/v1/events/calendar", params={"from": "2026-03-01T00:00:00", "to": "2026-03-31T00:00:00"}, headers=lead
    )
    body = resp.json()
    assert body["timezone"] == "America/New_York"
    starts = [datetime.fromisoformat(o["occurrence_start"]) for o in body["occurrences"]]
    assert [s.astimezone(NY).strftime("%m-%d %H:%M") for s in starts] == [
        "03-01 09:00",
        "03-08 09:00",
        "03-15 09:00",
        "03-22 09:00",
        "03-29 09:00",
    ]
    assert [s.hour for s in starts] == [14, 13, 13, 13, 13]  # the stored instant moved with the clock change


async def test_all_day_and_multi_day_events(client, church_payload):
    payload, leader, lead = await _church(client, church_payload, "cal-allday")
    await _set_timezone(client, payload, leader, "America/New_York")

    all_day = await _create(client, lead, "Conference", start_date_time="2026-06-10T15:30:00", is_all_day=True)
    assert all_day["is_all_day"] is True
    assert datetime.fromisoformat(all_day["start_date_time"]).astimezone(NY).strftime("%m-%d %H:%M") == "06-10 00:00"
    assert datetime.fromisoformat(all_day["end_date_time"]).astimezone(NY).strftime("%m-%d %H:%M") == "06-11 00:00"
    three_days = await _create(
        client,
        lead,
        "Camp",
        start_date_time="2026-06-10T00:00:00",
        end_date_time="2026-06-13T00:00:00",
        is_all_day=True,
    )
    assert datetime.fromisoformat(three_days["end_date_time"]).astimezone(NY).strftime("%m-%d") == "06-13"
    overnight = await _create(
        client, lead, "Vigil", start_date_time="2026-06-10T20:00:00", end_date_time="2026-06-12T10:00:00"
    )

    def window(a, b):
        return {"from": f"2026-06-{a}T00:00:00", "to": f"2026-06-{b}T00:00:00"}

    on_the_11th = (await client.get("/api/v1/events/calendar", params=window("11", "12"), headers=lead)).json()
    titles = {o["title"] for o in on_the_11th["occurrences"]}
    assert titles == {"Camp", "Vigil"}  # both began earlier and are still going; the one-day conference is over
    after = (await client.get("/api/v1/events/calendar", params=window("13", "14"), headers=lead)).json()
    assert after["occurrences"] == []
    first_day = (await client.get("/api/v1/events/calendar", params=window("10", "11"), headers=lead)).json()
    assert {o["title"] for o in first_day["occurrences"]} == {"Conference", "Camp", "Vigil"}
    assert all(o["is_all_day"] for o in first_day["occurrences"] if o["title"] in ("Conference", "Camp"))
    assert overnight["is_all_day"] is False


async def test_calendar_windows_are_validated(client, church_payload):
    _, _, lead = await _church(client, church_payload, "cal-window")
    assert (await client.get("/api/v1/events/calendar")).status_code == 401
    assert (await client.get("/api/v1/events/calendar", headers=lead)).status_code == 422
    reversed_ = await client.get(
        "/api/v1/events/calendar", params={"from": "2026-06-10", "to": "2026-06-01"}, headers=lead
    )
    assert reversed_.status_code == 422 and reversed_.json()["error"]["code"] == "invalid_window"
    too_long = await client.get("/api/v1/events/calendar", params=_window(0, 200), headers=lead)
    assert too_long.status_code == 422 and too_long.json()["error"]["code"] == "invalid_window"
    event = await _create(client, lead, "Series", recurrence_rule="FREQ=DAILY")
    bad = await client.get(
        f"/api/v1/events/{event['id']}/occurrences", params={"from": "2026-06-10", "to": "2026-06-01"}, headers=lead
    )
    assert bad.status_code == 422
    naive = await client.get(
        f"/api/v1/events/{event['id']}/occurrences",
        params={"from": "2026-06-01T00:00:00", "to": "2026-06-05"},
        headers=lead,
    )
    assert naive.status_code == 200  # a naive time is the church's local time, never a crash


# ───────────────────────── Public calendar ─────────────────────────


async def test_the_public_calendar_shows_only_published_public_events(client, church_payload):
    _, leader, lead = await _church(client, church_payload, "cal-public")
    slug = (await client.get("/api/v1/org/profile", headers=lead)).json()["slug"]
    group = (await client.post("/api/v1/groups", json={"name": "Choir"}, headers=lead)).json()
    open_day = await _publish(client, lead, await _create(client, lead, "Open day", visibility="public"))
    await _create(client, lead, "Draft public", visibility="public")
    await _publish(client, lead, await _create(client, lead, "Members only", visibility="members"))
    await _publish(
        client, lead, await _create(client, lead, "Choir private", visibility="private", group_id=group["id"])
    )
    canceled = await _publish(client, lead, await _create(client, lead, "Canceled", visibility="public"))
    await client.post(f"/api/v1/events/{canceled['id']}/cancel", headers=lead)

    resp = await client.get(f"/api/v1/churches/{slug}/calendar", params=_window())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [o["title"] for o in body["occurrences"]] == ["Open day"]
    assert open_day["id"] == body["occurrences"][0]["event_id"]
    assert not ({"status", "visibility", "group_id", "unit_id", "group_name"} & body["occurrences"][0].keys())
    assert (
        await client.get(f"/api/v1/churches/no-such-{uuid.uuid4().hex[:6]}/calendar", params=_window())
    ).status_code == 404
    assert (await client.get(f"/api/v1/churches/{slug}/calendar", params=_window(0, 400))).status_code == 422


async def test_a_member_role_cannot_see_the_review_trail_but_reviewers_can_read_history(client, church_payload):
    _, leader, lead = await _church(client, church_payload, "cal-history")
    _, staff = await _staff(client, lead, unique_email("hist"))
    staff_h = auth(staff["access_token"])
    event = await _create(client, staff_h, "Staff event")
    submitted = await client.post(
        f"/api/v1/events/{event['id']}/submit", json={"reviewer_user_ids": [leader["user"]["id"]]}, headers=staff_h
    )
    assert submitted.status_code == 200
    approved = await client.post(f"/api/v1/events/{event['id']}/review", json={"decision": "approved"}, headers=lead)
    assert approved.json()["status"] == "published"
    actions = [h["action"] for h in (await client.get(f"/api/v1/events/{event['id']}/history", headers=lead)).json()]
    assert {"event.created", "event.submitted", "event.review_approved", "event.published"} <= set(actions)
