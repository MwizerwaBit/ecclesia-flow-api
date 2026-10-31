"""Regression tests for defects found when the calendar work was reviewed: each one
stays fixed. (Helpers are shared with tests/test_calendar.py.)"""

import uuid
from datetime import UTC, datetime, timedelta

from tests.conftest import auth, unique_email
from tests.test_calendar import NY, _church, _create, _iso, _set_timezone, _window
from tests.test_features import _joined_member, _staff
from tests.test_foundation import _church as _foundation_church
from tests.test_foundation import _mfa, _scoped_staff, _unit


async def test_an_impossible_repeat_end_date_is_refused_and_a_bad_stored_rule_cannot_break_the_calendar(
    client, church_payload
):
    from app.core.database import platform_session

    _, _, lead = await _church(client, church_payload, "fix-until")
    for rule in ("FREQ=DAILY;UNTIL=20260230", "FREQ=DAILY;UNTIL=20261399", "FREQ=DAILY;UNTIL=20261231T256100Z"):
        bad = await client.post(
            "/api/v1/events",
            json={"title": "Daily", "type": "event", "start_date_time": _iso(2), "recurrence_rule": rule},
            headers=lead,
        )
        assert bad.status_code == 422, rule
    event = await _create(client, lead, "Series", recurrence_rule="FREQ=DAILY")
    # A rule saved before it was validated this strictly: it has no dates, but nothing else breaks.
    async with platform_session() as db:
        await db.execute_raw(
            "update events set recurrence_rule = 'FREQ=DAILY;UNTIL=20260230' where id = $1::uuid", event["id"]
        )
    assert (await client.get("/api/v1/events", headers=lead)).status_code == 200
    assert (await client.get(f"/api/v1/events/{event['id']}", headers=lead)).status_code == 200
    window = await client.get("/api/v1/events/calendar", params=_window(), headers=lead)
    assert window.status_code == 200 and window.json()["occurrences"] == []


async def test_nulls_extreme_dates_and_bad_ids_answer_422_not_500(client, church_payload):
    payload, leader, lead = await _church(client, church_payload, "fix-422")
    event = await _create(client, lead, "One")
    url = f"/api/v1/events/{event['id']}"
    for body in ({"visibility": None}, {"attendance_mode": None}, {"is_all_day": None}):
        assert (await client.patch(url, json=body, headers=lead)).status_code == 422, body

    early = {"from": "0001-01-01T00:00:00+02:00", "to": "0001-02-01T00:00:00+02:00"}
    assert (await client.get("/api/v1/events/calendar", params=early, headers=lead)).status_code == 422
    for bad in ({"group_id": "not-a-uuid"}, {"unit_id": "nope"}, {"status": "weird"}, {"type": "party"}):
        resp = await client.get("/api/v1/events/calendar", params={**_window(), **bad}, headers=lead)
        assert resp.status_code == 422, bad
    too_early = await client.post(
        "/api/v1/events",
        json={"title": "Old", "type": "event", "start_date_time": "0001-01-01T00:00:00+02:00"},
        headers=lead,
    )
    assert too_early.status_code == 422

    verified = auth((await _mfa(client, payload, leader))["access_token"])
    for zone in (None, "UTC+3", "XYZ3", "a1", ""):
        resp = await client.patch("/api/v1/org/profile", json={"timezone": zone}, headers=verified)
        assert resp.status_code == 422, zone


async def test_saving_an_unchanged_form_changes_nothing_and_keeps_a_review_in_progress(client, church_payload):
    _, leader, lead = await _church(client, church_payload, "fix-noop")
    _, staff = await _staff(client, lead, unique_email("noop"))
    staff_h = auth(staff["access_token"])
    event = await _create(client, staff_h, "Draft")
    event_url = f"/api/v1/events/{event['id']}"
    submitted = await client.post(
        f"/api/v1/events/{event['id']}/submit", json={"reviewer_user_ids": [leader["user"]["id"]]}, headers=staff_h
    )
    assert submitted.json()["status"] == "pending_review"
    before = (await client.get(f"{event_url}/history", headers=lead)).json()

    # The form sends every field every time: the same values change nothing.
    same = await client.patch(
        event_url,
        json={
            "title": event["title"],
            "type": event["type"],
            "start_date_time": event["start_date_time"],
            "visibility": event["visibility"],
            "theme": event["theme"],
            "attendance_mode": event["attendance_mode"],
        },
        headers=staff_h,
    )
    assert same.status_code == 200 and same.json()["status"] == "pending_review"
    assert len(same.json()["reviews"]) == 1
    assert len((await client.get(f"{event_url}/history", headers=lead)).json()) == len(before)

    # A cosmetic change (the accent color) keeps the review; a change reviewers looked at resets it.
    themed = await client.patch(
        event_url, json={"theme": {"accent_color": "#7c3aed", "layout": "banner"}}, headers=staff_h
    )
    assert themed.json()["status"] == "pending_review" and len(themed.json()["reviews"]) == 1
    retitled = await client.patch(event_url, json={"title": "A different title"}, headers=staff_h)
    assert retitled.json()["status"] == "draft" and retitled.json()["reviews"] == []


async def test_flipping_only_the_all_day_flag_is_a_real_edit_for_the_stale_check(client, church_payload):
    _, _, lead = await _church(client, church_payload, "fix-flag")
    event = await _create(client, lead, "Fair", start_date_time="2026-06-10T09:00:00", is_all_day=True)
    url = f"/api/v1/events/{event['id']}"
    flipped = await client.patch(
        url, json={"is_all_day": False, "expected_updated_at": event["updated_at"]}, headers=lead
    )
    assert flipped.status_code == 200 and flipped.json()["is_all_day"] is False
    assert flipped.json()["updated_at"] != event["updated_at"]  # the flag edit stamps the row
    stale = await client.patch(url, json={"title": "Fair 2", "expected_updated_at": event["updated_at"]}, headers=lead)
    assert stale.status_code == 409


async def test_upcoming_and_past_lists_follow_what_is_still_to_come(client, church_payload):
    _, _, lead = await _church(client, church_payload, "fix-upcoming")
    today = datetime.now(UTC).date()
    long_ago = (today - timedelta(days=60)).isoformat()
    ended = await _create(
        client,
        lead,
        "Ended series",
        start_date_time=f"{long_ago}T09:00:00",
        recurrence_rule=f"FREQ=WEEKLY;UNTIL={(today - timedelta(days=30)).strftime('%Y%m%d')}",
    )
    await _create(client, lead, "Later one-off", start_date_time=_iso(20))
    weekly = await _create(
        client, lead, "Weekly", start_date_time=f"{long_ago}T09:00:00", recurrence_rule="FREQ=WEEKLY"
    )
    await _create(
        client,
        lead,
        "Camp in progress",
        start_date_time=f"{(today - timedelta(days=1)).isoformat()}T00:00:00",
        end_date_time=f"{(today + timedelta(days=2)).isoformat()}T00:00:00",
        is_all_day=True,
    )
    await _create(client, lead, "Long gone", start_date_time=_iso(-30))

    upcoming = (await client.get("/api/v1/events", params={"upcoming": "true"}, headers=lead)).json()
    titles = [e["title"] for e in upcoming]
    assert "Ended series" not in titles and "Long gone" not in titles
    assert {"Later one-off", "Weekly", "Camp in progress"} <= set(titles)  # an event in progress is still upcoming
    keys = [e["next_occurrence"] for e in upcoming]
    assert keys == sorted(keys)  # soonest first, by the next date, not by when a series began
    assert titles.index("Later one-off") > titles.index("Weekly")
    past_response = await client.get("/api/v1/events", params={"upcoming": "false"}, headers=lead)
    past = {e["title"] for e in past_response.json()}
    assert {"Ended series", "Long gone"} <= past and "Weekly" not in past and "Camp in progress" not in past
    assert (await client.get(f"/api/v1/events/{weekly['id']}", headers=lead)).json()["next_occurrence"] is not None
    assert (await client.get(f"/api/v1/events/{ended['id']}", headers=lead)).json()["next_occurrence"] is None


async def test_the_review_queue_places_a_series_in_the_church_zone(client, church_payload):
    payload, leader, lead = await _church(client, church_payload, "fix-queue")
    await _set_timezone(client, payload, leader, "America/New_York")
    _, staff = await _staff(client, lead, unique_email("queue"))
    staff_h = auth(staff["access_token"])
    event = await _create(
        client, staff_h, "Sunday", start_date_time="2026-01-04T09:00:00", recurrence_rule="FREQ=WEEKLY;BYDAY=SU"
    )
    await client.post(
        f"/api/v1/events/{event['id']}/submit", json={"reviewer_user_ids": [leader["user"]["id"]]}, headers=staff_h
    )
    queued = (await client.get("/api/v1/events/review-queue", headers=lead)).json()[0]
    listed = next(e for e in (await client.get("/api/v1/events", headers=lead)).json() if e["id"] == event["id"])
    assert queued["next_occurrence"] == listed["next_occurrence"]
    assert datetime.fromisoformat(queued["next_occurrence"]).astimezone(NY).strftime("%H:%M") == "09:00"


async def test_windows_are_half_open_and_the_occurrences_window_is_capped_below_the_expansion_limit(
    client, church_payload
):
    _, _, lead = await _church(client, church_payload, "fix-halfopen")
    opening = await _create(client, lead, "Starts as the window closes", start_date_time="2026-07-01T00:00:00Z")
    ending = await _create(
        client,
        lead,
        "Ends as it opens",
        start_date_time="2026-06-30T22:00:00Z",
        end_date_time="2026-07-01T00:00:00Z",
    )

    async def ids(a, b):
        resp = await client.get("/api/v1/events/calendar", params={"from": a, "to": b}, headers=lead)
        return {o["event_id"] for o in resp.json()["occurrences"]}

    assert await ids("2026-06-01T00:00:00Z", "2026-07-01T00:00:00Z") == {ending["id"]}  # not the boundary one
    assert await ids("2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z") == {opening["id"]}  # never in two windows

    daily = await _create(client, lead, "Daily", start_date_time="2026-01-01T09:00:00Z", recurrence_rule="FREQ=DAILY")
    url = f"/api/v1/events/{daily['id']}/occurrences"
    year = await client.get(
        url, params={"from": "2026-01-01T00:00:00Z", "to": "2026-12-31T23:59:59Z", "limit": 500}, headers=lead
    )
    assert year.status_code == 200 and len(year.json()) == 365  # none silently cut
    too_long = await client.get(
        url, params={"from": "2026-01-01T00:00:00Z", "to": "2027-03-01T00:00:00Z"}, headers=lead
    )
    assert too_long.status_code == 422


async def test_a_hidden_event_and_a_missing_one_look_the_same_to_a_member(client, church_payload):
    _, leader, lead = await _church(client, church_payload, "fix-same")
    slug = (await client.get("/api/v1/org/profile", headers=lead)).json()["slug"]
    member, _ = await _joined_member(client, slug, leader, "Sam", "SAM10003")
    member_h = auth(member["access_token"])
    draft = await _create(client, lead, "Secret draft")
    hidden = await client.get(f"/api/v1/events/{draft['id']}", headers=member_h)
    missing = await client.get(f"/api/v1/events/{uuid.uuid4()}", headers=member_h)
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"]["message"] == missing.json()["error"]["message"]


async def test_a_branch_staff_members_calendar_is_not_crowded_out_by_other_branches(client, church_payload):
    _, _, lead, root = await _foundation_church(client, church_payload, "fix-crowd")
    east, west = await _unit(client, lead, "East", root), await _unit(client, lead, "West", root)
    staff = await _scoped_staff(client, lead, east)
    for i in range(3):
        await _create(client, lead, f"West {i}", unit_id=west, recurrence_rule="FREQ=DAILY")
    mine = await _create(client, lead, "East service", unit_id=east)
    body = (await client.get("/api/v1/events/calendar", params=_window(), headers=staff)).json()
    assert {o["event_id"] for o in body["occurrences"]} == {mine["id"]} and body["truncated"] is False


async def test_the_public_event_says_how_it_repeats(client, church_payload):
    _, _, lead = await _church(client, church_payload, "fix-public-rule")
    slug = (await client.get("/api/v1/org/profile", headers=lead)).json()["slug"]
    event = await _create(client, lead, "Sunday", visibility="public", recurrence_rule="FREQ=WEEKLY;BYDAY=SU")
    assert (await client.post(f"/api/v1/events/{event['id']}/publish", headers=lead)).status_code == 200
    detail = (await client.get(f"/api/v1/churches/{slug}/events/{event['id']}")).json()
    assert detail["recurrence_rule"] == "FREQ=WEEKLY;BYDAY=SU" and detail["is_recurring"] is True
    listed = (await client.get(f"/api/v1/churches/{slug}/events")).json()
    assert listed[0]["recurrence_rule"] == "FREQ=WEEKLY;BYDAY=SU"
