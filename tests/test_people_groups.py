"""Integration tests for member registration and groups.

Same setup as test_auth_flow.py: runs against the dev Postgres with all
migrations applied (including 20261004090000_member_registration_and_groups);
every test registers its own church so tests never share data.
"""

import uuid


async def _church(client, church_payload, label: str) -> dict:
    resp = await client.post("/api/v1/auth/register", json=church_payload(label))
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _registration(**overrides) -> dict:
    body = {
        "first_name": "Grace",
        "last_name": "Uwase",
        "gender": "female",
        "phone": "+250 788 123 456",
        "email": "grace.uwase@example.org",
        "status": "active",
        "consent": {"given_by": "self", "communications": True, "directory_visible": True},
        "id_type": "national_id",
        "national_id": f"1199080{uuid.uuid4().int % 10**9:09d}",
    }
    body.update(overrides)
    return body


async def test_staff_role_has_group_permissions(client, church_payload):
    resp = await client.post("/api/v1/auth/register", json=church_payload("groupperms"))
    assert {"groups:read", "groups:manage"} <= set(resp.json()["permissions"])


async def test_registration_requires_gender_contact_and_consent(client, church_payload):
    headers = await _church(client, church_payload, "regrules")
    no_gender = _registration()
    del no_gender["gender"]
    no_contact = _registration(phone=None, email=None)
    no_consent = _registration()
    del no_consent["consent"]
    future_dob = _registration(date_of_birth="2999-01-01")
    for body in (no_gender, no_contact, no_consent, future_dob):
        resp = await client.post("/api/v1/members", json=body, headers=headers)
        assert resp.status_code == 422, body


async def test_registration_creates_household_groups_and_consent_in_one_go(client, church_payload):
    headers = await _church(client, church_payload, "regfull")
    group = await client.post(
        "/api/v1/groups",
        json={
            "name": "Youth Fellowship",
            "type": "fellowship",
            "schedule": {"frequency": "weekly", "day": "saturday", "time": "16:00"},
        },
        headers=headers,
    )
    assert group.status_code == 201
    group_id = group.json()["id"]

    resp = await client.post(
        "/api/v1/members",
        json=_registration(
            date_of_birth="2012-03-04",
            envelope_number="0001",
            household={"mode": "new", "name": "The Uwase Household"},
            household_role="child",
            emergency_contact={"name": "Jeanne Uwase", "phone": "+250788000111", "relationship": "Mother"},
            consent={"given_by": "guardian", "communications": False, "directory_visible": False},
            group_ids=[group_id],
        ),
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    member = resp.json()
    assert member["household_id"]
    assert member["consent"]["given_by"] == "guardian"
    assert member["consent"]["data_processing_at"]
    assert member["emergency_contact"]["name"] == "Jeanne Uwase"
    assert [g["id"] for g in member["groups"]] == [group_id]

    detail = (await client.get(f"/api/v1/groups/{group_id}", headers=headers)).json()
    assert [e["member_id"] for e in detail["roster"]] == [member["id"]]

    roster = (await client.get("/api/v1/members", headers=headers)).json()
    assert roster[0]["groups"][0]["name"] == "Youth Fellowship"


async def test_envelope_numbers_are_unique_per_church_and_suggested(client, church_payload):
    headers = await _church(client, church_payload, "envelope")
    first = await client.post("/api/v1/members", json=_registration(envelope_number="0042"), headers=headers)
    assert first.status_code == 200
    clash = await client.post(
        "/api/v1/members",
        json=_registration(
            first_name="Other", email="other@example.org", phone="+250788999999", envelope_number="0042"
        ),
        headers=headers,
    )
    assert clash.status_code == 409
    suggested = await client.get("/api/v1/members/next-envelope-number", headers=headers)
    assert suggested.json()["envelope_number"] == "0043"

    # A different church may reuse the same number.
    other = await _church(client, church_payload, "envelope2")
    reuse = await client.post("/api/v1/members", json=_registration(envelope_number="0042"), headers=other)
    assert reuse.status_code == 200


async def test_duplicates_are_found_by_name_phone_or_email(client, church_payload):
    headers = await _church(client, church_payload, "dupes")
    await client.post("/api/v1/members", json=_registration(), headers=headers)

    by_phone = await client.get("/api/v1/members/duplicates", params={"phone": "0788123456"}, headers=headers)
    assert by_phone.json()[0]["reasons"] == ["phone"]
    by_name = await client.get(
        "/api/v1/members/duplicates", params={"first_name": "grace", "last_name": "UWASE"}, headers=headers
    )
    assert "name" in by_name.json()[0]["reasons"]
    nothing = await client.get("/api/v1/members/duplicates", params={"email": "nobody@example.org"}, headers=headers)
    assert nothing.json() == []


async def test_one_church_never_sees_or_touches_anothers_people_or_groups(client, church_payload):
    a = await _church(client, church_payload, "isoA")
    b = await _church(client, church_payload, "isoB")

    member = (await client.post("/api/v1/members", json=_registration(), headers=a)).json()
    group = (await client.post("/api/v1/groups", json={"name": "Choir"}, headers=a)).json()

    assert (await client.get("/api/v1/members", headers=b)).json() == []
    assert (await client.get("/api/v1/groups", headers=b)).json() == []
    assert (await client.get(f"/api/v1/members/{member['id']}", headers=b)).status_code == 404
    assert (await client.get(f"/api/v1/groups/{group['id']}", headers=b)).status_code == 404
    assert (await client.get("/api/v1/members/duplicates", params={"phone": "+250788123456"}, headers=b)).json() == []

    # B can't add A's member to B's own group: the id simply isn't a member here.
    b_group = (await client.post("/api/v1/groups", json={"name": "Choir"}, headers=b)).json()
    added = await client.post(f"/api/v1/groups/{b_group['id']}/members", json={"member_ids": [member["id"]]}, headers=b)
    assert added.status_code == 200
    assert added.json() == []


async def test_group_roster_roles_and_archiving(client, church_payload):
    headers = await _church(client, church_payload, "roster")
    m = (await client.post("/api/v1/members", json=_registration(), headers=headers)).json()
    g = (await client.post("/api/v1/groups", json={"name": "Media Team", "type": "team"}, headers=headers)).json()

    dup_name = await client.post("/api/v1/groups", json={"name": "media team"}, headers=headers)
    assert dup_name.status_code == 409

    await client.post(f"/api/v1/groups/{g['id']}/members", json={"member_ids": [m["id"]]}, headers=headers)
    promoted = await client.patch(
        f"/api/v1/groups/{g['id']}/members/{m['id']}", json={"role": "leader", "note": "Livestream"}, headers=headers
    )
    assert promoted.json()["role"] == "leader"
    detail = (await client.get(f"/api/v1/groups/{g['id']}", headers=headers)).json()
    assert detail["leaders"][0]["id"] == m["id"]
    assert detail["member_count"] == 1

    await client.patch(f"/api/v1/groups/{g['id']}", json={"is_archived": True}, headers=headers)
    assert (await client.get("/api/v1/groups", headers=headers)).json() == []
    # Archiving frees the name for a new group.
    again = await client.post("/api/v1/groups", json={"name": "Media Team"}, headers=headers)
    assert again.status_code == 201

    removed = await client.delete(f"/api/v1/groups/{g['id']}/members/{m['id']}", headers=headers)
    assert removed.status_code == 204
