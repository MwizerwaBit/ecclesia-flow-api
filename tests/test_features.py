"""Leadership structure, ID numbers + self-registration, group invitations and
the member portal, and the event review workflow — each with its
authorization edges."""

from datetime import UTC, datetime, timedelta

from app.modules.rbac.models import SYSTEM_ROLE_BOARD_ID, SYSTEM_ROLE_STAFF_ID
from tests.conftest import accept_invite, adult_member, auth


async def _church(client, church_payload, label):
    payload = church_payload(label)
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200
    session = resp.json()
    return payload, session, auth(session["access_token"])


async def _staff(client, lead_headers, email, unit_scope=None):
    invite = await client.post(
        "/api/v1/team/invite",
        json={"email": email, "role_id": SYSTEM_ROLE_STAFF_ID, **({"unit_scope": unit_scope} if unit_scope else {})},
        headers=lead_headers,
    )
    assert invite.status_code == 200, invite.text
    session = await accept_invite(client, invite.json()["invite_url"], first="Sam", last="Staff")
    return invite.json()["id"], session


# ───────────────────────────── Leadership ─────────────────────────────


async def test_leadership_positions_carry_access_and_only_the_leader_shapes_them(client, church_payload):
    _, leader, lead = await _church(client, church_payload, "lead")
    units = (await client.get("/api/v1/hierarchy/units", headers=lead)).json()
    branch = (
        await client.post(
            "/api/v1/hierarchy/units",
            json={"name": "Eastside", "type": "Branch", "parent_id": units[0]["id"]},
            headers=lead,
        )
    ).json()

    root = (await client.post("/api/v1/leadership/positions", json={"title": "Senior Pastor"}, headers=lead)).json()
    youth = await client.post(
        "/api/v1/leadership/positions",
        json={
            "title": "Youth Pastor",
            "parent_id": root["id"],
            "role_id": SYSTEM_ROLE_STAFF_ID,
            "unit_id": branch["id"],
        },
        headers=lead,
    )
    assert youth.status_code == 201, youth.text
    youth = youth.json()
    protected = await client.post(
        "/api/v1/leadership/positions", json={"title": "Co-admin", "role_id": SYSTEM_ROLE_BOARD_ID}, headers=lead
    )
    assert protected.status_code == 403
    cycle = await client.patch(
        f"/api/v1/leadership/positions/{root['id']}", json={"parent_id": youth["id"]}, headers=lead
    )
    assert cycle.status_code == 409

    membership_id, staff = await _staff(client, lead, f"youth.{leader['user']['id'][:6]}@example-church.org")
    assigned = await client.post(
        f"/api/v1/leadership/positions/{youth['id']}/holders", json={"membership_id": membership_id}, headers=lead
    )
    assert assigned.status_code == 200, assigned.text
    assert [h["membership_id"] for h in assigned.json()["holders"]] == [membership_id]

    # Their access changed, so their old token is stale; a refresh carries the branch scope.
    assert (await client.get("/api/v1/members", headers=auth(staff["access_token"]))).status_code == 401
    refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": staff["refresh_token"]})
    assert refreshed.status_code == 200, refreshed.text
    refreshed = refreshed.json()
    assert refreshed["unit_scope_id"] == branch["id"]

    # Only the leader holds leadership:manage.
    staff_try = await client.post(
        "/api/v1/leadership/positions", json={"title": "Me"}, headers=auth(refreshed["access_token"])
    )
    assert staff_try.status_code == 403

    removed = await client.delete(f"/api/v1/leadership/positions/{youth['id']}/holders/{membership_id}", headers=lead)
    assert removed.status_code == 204
    after = (await client.post("/api/v1/auth/refresh", json={"refresh_token": refreshed["refresh_token"]})).json()
    assert after["unit_scope_id"] is None


# ───────────────────────────── ID numbers + join ─────────────────────────────


async def test_id_numbers_are_required_protected_and_unique(client, church_payload):
    _, _, lead = await _church(client, church_payload, "ids")
    no_id = adult_member("Nora")
    del no_id["national_id"]
    assert (await client.post("/api/v1/members", json=no_id, headers=lead)).status_code == 422
    child = {**no_id, "date_of_birth": (datetime.now(UTC) - timedelta(days=365 * 9)).date().isoformat()}
    assert (await client.post("/api/v1/members", json=child, headers=lead)).status_code == 200

    body = adult_member("Ada", national_id="1 1990 8 0012345 0 12")
    created = (await client.post("/api/v1/members", json=body, headers=lead)).json()
    assert created["national_id_last4"] == "5012"
    assert "national_id" not in created and "national_id_hash" not in created

    same_number_differently_typed = adult_member("Other", national_id="11990800123450-12")
    dup = await client.post("/api/v1/members", json=same_number_differently_typed, headers=lead)
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "duplicate_id"


async def test_joining_links_to_the_existing_record_only_when_id_and_name_match(client, church_payload):
    _, leader, lead = await _church(client, church_payload, "join")
    slug = (await client.get("/api/v1/org/profile", headers=lead)).json()["slug"]
    existing = (
        await client.post(
            "/api/v1/members", json=adult_member("Grace", "Uwase", national_id="ID-7788-9900"), headers=lead
        )
    ).json()

    def join(**over):
        return {
            "church_slug": slug,
            "first_name": "Grace",
            "last_name": "Uwase",
            "email": f"grace.{over.pop('tag', 'a')}.{leader['user']['id'][:6]}@example-church.org",
            "password": "JoinChurch2026",
            "phone": "+250788111222",
            "gender": "female",
            "date_of_birth": "1990-05-05",
            "national_id": "ID77889900",
            "consent_data_processing": True,
            **over,
        }

    minor = await client.post("/api/v1/auth/join", json=join(tag="m", date_of_birth="2015-01-01"))
    assert minor.status_code == 422
    wrong_name = await client.post("/api/v1/auth/join", json=join(tag="w", first_name="Mallory"))
    assert wrong_name.status_code == 409 and wrong_name.json()["error"]["code"] == "identity_mismatch"

    joined = await client.post("/api/v1/auth/join", json=join(tag="ok"))
    assert joined.status_code == 200, joined.text
    me = await client.get("/api/v1/me/member", headers=auth(joined.json()["access_token"]))
    assert me.json()["id"] == existing["id"]  # linked, not duplicated
    assert joined.json()["role"] == "member"

    taken = await client.post("/api/v1/auth/join", json=join(tag="again"))
    assert taken.status_code == 409  # that record already belongs to a login

    newcomer = await client.post(
        "/api/v1/auth/join", json=join(tag="new", first_name="New", last_name="Comer", national_id="PX123456")
    )
    assert newcomer.status_code == 200
    mine = (await client.get("/api/v1/me/member", headers=auth(newcomer.json()["access_token"]))).json()
    assert mine["status"] == "visitor" and mine["national_id_last4"] == "3456"

    # A member can't read the directory.
    assert (await client.get("/api/v1/members", headers=auth(newcomer.json()["access_token"]))).status_code == 403


# ───────────────────────────── Groups: invitations + portal ─────────────────────────────


async def _joined_member(client, slug, leader, first, national_id):
    resp = await client.post(
        "/api/v1/auth/join",
        json={
            "church_slug": slug,
            "first_name": first,
            "last_name": "Member",
            "email": f"{first.lower()}.{leader['user']['id'][:6]}@example-church.org",
            "password": "JoinChurch2026",
            "phone": "+250788333444",
            "gender": "male",
            "date_of_birth": "1985-01-01",
            "national_id": national_id,
            "consent_data_processing": True,
        },
    )
    assert resp.status_code == 200, resp.text
    session = resp.json()
    member = (await client.get("/api/v1/me/member", headers=auth(session["access_token"]))).json()
    return session, member


async def test_group_invitations_and_leaders_running_their_group_from_the_portal(client, church_payload):
    _, leader, lead = await _church(client, church_payload, "grp")
    slug = (await client.get("/api/v1/org/profile", headers=lead)).json()["slug"]
    lea, lea_member = await _joined_member(client, slug, leader, "Leah", "LEAH0001")
    ben, ben_member = await _joined_member(client, slug, leader, "Ben", "BEN00002")
    offline = (await client.post("/api/v1/members", json=adult_member("Offline"), headers=lead)).json()

    secretary = await client.post(
        "/api/v1/groups/roles", json={"name": "Secretary", "capabilities": ["message"], "rank": 20}, headers=lead
    )
    assert secretary.status_code == 201 and secretary.json()["key"] == "secretary"

    group = (await client.post("/api/v1/groups", json={"name": "Choir"}, headers=lead)).json()
    other = (await client.post("/api/v1/groups", json={"name": "Ushers"}, headers=lead)).json()
    added = (
        await client.post(
            f"/api/v1/groups/{group['id']}/members",
            json={"member_ids": [lea_member["id"]], "role": "leader"},
            headers=lead,
        )
    ).json() + (
        await client.post(
            f"/api/v1/groups/{group['id']}/members",
            json={"member_ids": [offline["id"]], "role": "member"},
            headers=lead,
        )
    ).json()
    statuses = {m["member_id"]: m["status"] for m in added}
    assert statuses == {lea_member["id"]: "invited", offline["id"]: "active"}  # no login → joins directly

    lea_h = auth(lea["access_token"])
    mine = (await client.get("/api/v1/me/groups", headers=lea_h)).json()
    assert [(g["name"], g["status"]) for g in mine] == [("Choir", "invited")]  # never sees Ushers
    assert (await client.get(f"/api/v1/me/groups/{group['id']}", headers=lea_h)).status_code == 404  # not yet a member
    assert (await client.get(f"/api/v1/me/groups/{other['id']}", headers=lea_h)).status_code == 404

    accepted = await client.post(
        f"/api/v1/me/groups/invitations/{mine[0]['membership_id']}", json={"accept": True}, headers=lea_h
    )
    assert accepted.json()[0]["status"] == "active"

    detail = (await client.get(f"/api/v1/me/groups/{group['id']}", headers=lea_h)).json()
    assert detail["my_role"]["key"] == "leader" and "manage_roster" in detail["my_role"]["capabilities"]
    found = (
        await client.get(f"/api/v1/me/groups/{group['id']}/candidates", params={"search": "be"}, headers=lea_h)
    ).json()
    assert [c["id"] for c in found] == [ben_member["id"]]
    invited = await client.post(
        f"/api/v1/me/groups/{group['id']}/invitations",
        json={"member_ids": [ben_member["id"]], "role": "assistant"},
        headers=lea_h,
    )
    assert invited.status_code == 200

    ben_h = auth(ben["access_token"])
    ben_invite = (await client.get("/api/v1/me/groups", headers=ben_h)).json()[0]
    await client.post(
        f"/api/v1/me/groups/invitations/{ben_invite['membership_id']}", json={"accept": True}, headers=ben_h
    )

    # An assistant can manage the roster, but only below their own rank.
    promote_self_peer = await client.patch(
        f"/api/v1/me/groups/{group['id']}/members/{offline['id']}", json={"role": "leader"}, headers=ben_h
    )
    assert promote_self_peer.status_code == 403
    demote_leader = await client.delete(f"/api/v1/me/groups/{group['id']}/members/{lea_member['id']}", headers=ben_h)
    assert demote_leader.status_code == 403
    # The assistant doesn't get roster contact details; the leader does.
    ben_view = (await client.get(f"/api/v1/me/groups/{group['id']}", headers=ben_h)).json()
    assert all(
        p["phone"] is not None or p["member_id"] == offline["id"]
        for p in (await client.get(f"/api/v1/me/groups/{group['id']}", headers=lea_h)).json()["roster"]
    )
    assert ben_view["my_role"]["key"] == "assistant"

    last_leader_leaves = await client.delete(
        f"/api/v1/me/groups/{group['id']}/members/{lea_member['id']}", headers=lea_h
    )
    assert last_leader_leaves.status_code == 409


# ───────────────────────────── Events: review workflow ─────────────────────────────


async def test_events_go_through_review_and_respect_visibility(client, church_payload):
    _, leader, lead = await _church(client, church_payload, "evt")
    slug = (await client.get("/api/v1/org/profile", headers=lead)).json()["slug"]
    _, staff = await _staff(client, lead, f"events.{leader['user']['id'][:6]}@example-church.org")
    staff_h = auth(staff["access_token"])
    member, _ = await _joined_member(client, slug, leader, "Mia", "MIA00003")
    member_h = auth(member["access_token"])

    start = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    bad_cover = await client.post(
        "/api/v1/events",
        json={
            "title": "Harvest",
            "type": "service",
            "start_date_time": start.isoformat(),
            "cover_image_url": "javascript:alert(1)",
        },
        headers=staff_h,
    )
    assert bad_cover.status_code == 422
    bad_rule = await client.post(
        "/api/v1/events",
        json={
            "title": "Harvest",
            "type": "service",
            "start_date_time": start.isoformat(),
            "recurrence_rule": "FREQ=SECONDLY",
        },
        headers=staff_h,
    )
    assert bad_rule.status_code == 422

    created = await client.post(
        "/api/v1/events",
        json={
            "title": "Sunday Worship",
            "type": "service",
            "start_date_time": start.isoformat(),
            "visibility": "public",
            "recurrence_rule": "FREQ=WEEKLY;BYDAY=SU",
            "theme": {"accent_color": "#7c3aed", "layout": "banner"},
        },
        headers=staff_h,
    )
    assert created.status_code == 200, created.text
    event = created.json()
    assert event["status"] == "draft" and event["is_recurring"] and len(event["upcoming_occurrences"]) >= 4

    # Staff can't publish their own event; they send it for review.
    assert (await client.post(f"/api/v1/events/{event['id']}/publish", headers=staff_h)).status_code == 403
    options = (await client.get("/api/v1/events/reviewer-options", headers=staff_h)).json()
    assert [o["user_id"] for o in options] == [leader["user"]["id"]]
    self_review = await client.post(
        f"/api/v1/events/{event['id']}/submit", json={"reviewer_user_ids": [staff["user"]["id"]]}, headers=staff_h
    )
    assert self_review.status_code == 409
    submitted = await client.post(
        f"/api/v1/events/{event['id']}/submit", json={"reviewer_user_ids": [leader["user"]["id"]]}, headers=staff_h
    )
    assert submitted.json()["status"] == "pending_review"

    assert [e["id"] for e in (await client.get("/api/v1/me/events", headers=member_h)).json()] == []
    assert (await client.get(f"/api/v1/churches/{slug}/events")).json() == []

    queue = (await client.get("/api/v1/events/review-queue", headers=lead)).json()
    assert [e["id"] for e in queue] == [event["id"]]
    needs_reason = await client.post(
        f"/api/v1/events/{event['id']}/review", json={"decision": "changes_requested"}, headers=lead
    )
    assert needs_reason.status_code == 422
    changes = await client.post(
        f"/api/v1/events/{event['id']}/review",
        json={"decision": "changes_requested", "comment": "Add a cover"},
        headers=lead,
    )
    assert changes.json()["status"] == "changes_requested"

    await client.patch(
        f"/api/v1/events/{event['id']}", json={"cover_image_url": "https://example.org/c.jpg"}, headers=staff_h
    )
    await client.post(
        f"/api/v1/events/{event['id']}/submit", json={"reviewer_user_ids": [leader["user"]["id"]]}, headers=staff_h
    )
    approved = await client.post(f"/api/v1/events/{event['id']}/review", json={"decision": "approved"}, headers=lead)
    assert approved.json()["status"] == "published"

    assert [e["id"] for e in (await client.get("/api/v1/me/events", headers=member_h)).json()] == [event["id"]]
    public = (await client.get(f"/api/v1/churches/{slug}/events")).json()
    assert public[0]["cover_image_url"] == "https://example.org/c.jpg" and public[0]["theme"]["layout"] == "banner"

    occurrences = await client.get(
        f"/api/v1/events/{event['id']}/occurrences",
        params={"from": start.isoformat(), "to": (start + timedelta(days=28)).isoformat()},
        headers=staff_h,
    )
    assert len(occurrences.json()) in (4, 5)

    # Private events are for their group only.
    group = (await client.post("/api/v1/groups", json={"name": "Elders"}, headers=lead)).json()
    private = (
        await client.post(
            "/api/v1/events",
            json={
                "title": "Elders meeting",
                "type": "meeting",
                "start_date_time": start.isoformat(),
                "visibility": "private",
                "group_id": group["id"],
            },
            headers=lead,
        )
    ).json()
    await client.post(f"/api/v1/events/{private['id']}/publish", headers=lead)
    assert private["id"] not in [e["id"] for e in (await client.get("/api/v1/me/events", headers=member_h)).json()]
    assert private["id"] not in [e["id"] for e in (await client.get(f"/api/v1/churches/{slug}/events")).json()]


async def test_schedule_collisions_count_shared_members_and_flag_leaders(client, church_payload):
    """Someone who leads both a choir and a youth group can't be in two
    places — the check is about that overlap, not just "two events at once"
    (two unrelated groups meeting at the same time isn't anyone's problem)."""
    from app.core.database import platform_session

    _, leader, lead = await _church(client, church_payload, "collide")
    slug = (await client.get("/api/v1/org/profile", headers=lead)).json()["slug"]
    tenant_id = leader["active_membership"]["tenant_id"]

    leah, leah_member = await _joined_member(client, slug, leader, "Leah", "LEAH0099")
    leah_h = auth(leah["access_token"])
    bystander = (await client.post("/api/v1/members", json=adult_member("Bystander"), headers=lead)).json()
    outsider = (await client.post("/api/v1/members", json=adult_member("Outsider"), headers=lead)).json()

    choir = (await client.post("/api/v1/groups", json={"name": "Choir"}, headers=lead)).json()
    youth = (await client.post("/api/v1/groups", json={"name": "Youth"}, headers=lead)).json()
    unrelated = (await client.post("/api/v1/groups", json={"name": "Ushers"}, headers=lead)).json()

    # Leah leads both choir and youth — she's the collision's whole point.
    await client.post(
        f"/api/v1/groups/{choir['id']}/members",
        json={"member_ids": [leah_member["id"]], "role": "leader"},
        headers=lead,
    )
    await client.post(
        f"/api/v1/groups/{youth['id']}/members",
        json={"member_ids": [leah_member["id"]], "role": "leader"},
        headers=lead,
    )
    mine = (await client.get("/api/v1/me/groups", headers=leah_h)).json()
    for g in mine:
        await client.post(f"/api/v1/me/groups/invitations/{g['membership_id']}", json={"accept": True}, headers=leah_h)
    # bystander is in both groups too (counts toward "affected"); outsider is
    # only in the unrelated group (never shows up in either count).
    await client.post(
        f"/api/v1/groups/{choir['id']}/members", json={"member_ids": [bystander["id"]], "role": "member"}, headers=lead
    )
    await client.post(
        f"/api/v1/groups/{youth['id']}/members", json={"member_ids": [bystander["id"]], "role": "member"}, headers=lead
    )
    await client.post(
        f"/api/v1/groups/{unrelated['id']}/members",
        json={"member_ids": [outsider["id"]], "role": "member"},
        headers=lead,
    )

    start = (datetime.now(UTC) + timedelta(days=5)).replace(microsecond=0)
    choir_event = (
        await client.post(
            "/api/v1/events",
            json={
                "title": "Choir practice",
                "type": "meeting",
                "group_id": choir["id"],
                "start_date_time": start.isoformat(),
                "end_date_time": (start + timedelta(hours=2)).isoformat(),
            },
            headers=lead,
        )
    ).json()
    # Overlaps by 30 minutes.
    youth_start = start + timedelta(hours=1, minutes=30)
    youth_event = (
        await client.post(
            "/api/v1/events",
            json={
                "title": "Youth night",
                "type": "meeting",
                "group_id": youth["id"],
                "start_date_time": youth_start.isoformat(),
                "end_date_time": (youth_start + timedelta(hours=2)).isoformat(),
            },
            headers=lead,
        )
    ).json()
    # Ushers meets at the exact same time as choir, but shares no members — no collision.
    await client.post(
        "/api/v1/events",
        json={
            "title": "Ushers briefing",
            "type": "meeting",
            "group_id": unrelated["id"],
            "start_date_time": start.isoformat(),
            "end_date_time": (start + timedelta(hours=1)).isoformat(),
        },
        headers=lead,
    )

    collisions = await client.post(
        "/api/v1/events/collisions",
        json={
            "group_id": youth["id"],
            "start_date_time": youth_event["start_date_time"],
            "end_date_time": youth_event["end_date_time"],
            "exclude_event_id": youth_event["id"],
        },
        headers=lead,
    )
    assert collisions.status_code == 200, collisions.text
    found = collisions.json()
    assert [c["event_id"] for c in found] == [choir_event["id"]]
    assert found[0]["affected_member_count"] == 2  # Leah + bystander, not outsider
    assert [kp["member_id"] for kp in found[0]["key_participants"]] == [leah_member["id"]]

    notified = await client.post(
        "/api/v1/events/notify-conflict",
        json={
            "event_id": youth_event["id"],
            "colliding_event_id": choir_event["id"],
            "member_ids": [leah_member["id"]],
        },
        headers=lead,
    )
    assert notified.status_code == 200 and notified.json() == 1

    async with platform_session() as db:
        rows = await db.query_raw(
            "select title, body, action_url from notifications where tenant_id = $1::uuid and user_id = $2::uuid",
            tenant_id,
            leah["user"]["id"],
        )
        assert len(rows) == 1
        assert "Choir practice" in rows[0]["body"] and "Youth night" in rows[0]["body"]


# ───────────────────────────── Registration forms ─────────────────────────────


async def test_event_registration_fields_and_public_signup(client, church_payload):
    """Attach custom questions to an event, publish it, and let an anonymous
    visitor answer them through the public share link — welcoming, not
    interrogating: nothing here requires a login to RSVP."""
    _, leader, lead = await _church(client, church_payload, "forms")
    slug = (await client.get("/api/v1/org/profile", headers=lead)).json()["slug"]

    start = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    event = (
        await client.post(
            "/api/v1/events",
            json={"title": "Youth Camp", "type": "event", "start_date_time": start.isoformat(), "visibility": "public"},
            headers=lead,
        )
    ).json()
    assert (await client.post(f"/api/v1/events/{event['id']}/publish", headers=lead)).status_code == 200

    assert (await client.get(f"/api/v1/events/{event['id']}/fields", headers=lead)).json() == []

    fields = await client.put(
        f"/api/v1/events/{event['id']}/fields",
        json={
            "fields": [
                {
                    "label": "T-shirt size",
                    "field_type": "single_choice",
                    "options": ["S", "M", "L"],
                    "required": True,
                },
                {"label": "Dietary notes", "field_type": "text", "required": False},
                {
                    "label": "Allergies",
                    "field_type": "checkbox",
                    "options": ["Nuts", "Dairy", "Gluten"],
                    "required": False,
                },
            ]
        },
        headers=lead,
    )
    assert fields.status_code == 200, fields.text
    assert [f["label"] for f in fields.json()] == ["T-shirt size", "Dietary notes", "Allergies"]

    # The public share link shows the questions before anyone RSVPs — no auth.
    detail = await client.get(f"/api/v1/churches/{slug}/events/{event['id']}")
    assert detail.status_code == 200, detail.text
    assert [f["label"] for f in detail.json()["registration_fields"]] == ["T-shirt size", "Dietary notes", "Allergies"]

    # A required question left blank is rejected.
    missing = await client.post(
        f"/api/v1/churches/{slug}/events/{event['id']}/register",
        json={"respondent_name": "Casey Visitor", "answers": {}},
    )
    assert missing.status_code == 400

    submitted = await client.post(
        f"/api/v1/churches/{slug}/events/{event['id']}/register",
        json={
            "respondent_name": "Casey Visitor",
            "respondent_email": "casey@example.com",
            "answers": {"T-shirt size": "M", "Allergies": ["Nuts"]},
        },
    )
    assert submitted.status_code == 201, submitted.text

    # Staff running the event see it in the responses tab.
    responses = await client.get(f"/api/v1/events/{event['id']}/responses", headers=lead)
    assert responses.status_code == 200
    assert len(responses.json()) == 1
    row = responses.json()[0]
    assert row["respondent_name"] == "Casey Visitor"
    assert row["answers"]["T-shirt size"] == "M" and row["answers"]["Allergies"] == ["Nuts"]

    # Editing the form afterward doesn't corrupt what was already collected —
    # answers are snapshotted by label, not tied back to the field row.
    await client.put(f"/api/v1/events/{event['id']}/fields", json={"fields": []}, headers=lead)
    still_there = (await client.get(f"/api/v1/events/{event['id']}/responses", headers=lead)).json()
    assert still_there[0]["answers"]["T-shirt size"] == "M"
