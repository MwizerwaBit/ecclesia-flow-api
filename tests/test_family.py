"""Family relationships: recording them, and the access they do and don't grant.

The security-relevant claims, each pinned by a test below:
  - a parent or guardian reaches a child's narrow view, and only while the
    child is under 18;
  - a spouse relationship grants nothing;
  - someone else's child is a 404, the same as a person who doesn't exist;
  - the view leaves out giving, notes and ID details;
  - the record itself can't be made nonsensical (self, minor parent, loops,
    two spouses, duplicates) and can't cross churches.
"""

from datetime import UTC, datetime, timedelta

from tests.conftest import adult_member, auth
from tests.test_features import _church, _joined_member


def _years_ago(years: int) -> str:
    return (datetime.now(UTC) - timedelta(days=365 * years + years // 4 + 30)).date().isoformat()


def _child(first: str, years: int = 9, **extra) -> dict:
    body = adult_member(first, "Kid", date_of_birth=_years_ago(years), consent={"given_by": "guardian"})
    del body["national_id"]  # minors can be registered without an ID
    body.update(extra)
    return body


async def _relate(client, headers, member_id, other_id, role):
    return await client.post(
        f"/api/v1/members/{member_id}/relationships",
        json={"related_member_id": other_id, "role": role},
        headers=headers,
    )


async def _setup(client, church_payload, label):
    _, leader, lead = await _church(client, church_payload, label)
    slug = (await client.get("/api/v1/org/profile", headers=lead)).json()["slug"]
    return leader, lead, slug


async def test_parent_reaches_their_minor_child_and_nobody_else_does(client, church_payload):
    leader, lead, slug = await _setup(client, church_payload, "fam1")
    mum, mum_member = await _joined_member(client, slug, leader, "Mary", "MARY0001")
    stranger, _ = await _joined_member(client, slug, leader, "Sam", "SAM00002")
    kid = (await client.post("/api/v1/members", json=_child("Timmy"), headers=lead)).json()

    created = await _relate(client, lead, kid["id"], mum_member["id"], "parent")
    assert created.status_code == 201, created.text
    assert created.json()[0]["role"] == "parent"

    mine = (await client.get("/api/v1/me/family", headers=auth(mum["access_token"]))).json()
    assert [d["other_member_id"] for d in mine["dependants"]] == [kid["id"]]
    assert mine["dependants"][0]["role"] == "child" and mine["dependants"][0]["grants_access"] is True

    view = await client.get(f"/api/v1/me/family/{kid['id']}", headers=auth(mum["access_token"]))
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["first_name"] == "Timmy" and body["age"] == 9
    for forbidden in ("notes", "total_giving", "giving_this_year", "national_id_last4", "id_type", "consent"):
        assert forbidden not in body

    # Not their child: the same 404 as a person who doesn't exist.
    other = await client.get(f"/api/v1/me/family/{kid['id']}", headers=auth(stranger["access_token"]))
    assert other.status_code == 404
    missing = await client.get(
        "/api/v1/me/family/00000000-0000-4000-8000-000000000000", headers=auth(stranger["access_token"])
    )
    assert missing.status_code == 404

    # Staff see it from the child's side too, with the inverse role.
    listed = (await client.get(f"/api/v1/members/{mum_member['id']}/relationships", headers=lead)).json()
    assert [r["role"] for r in listed] == ["child"]


async def test_a_spouse_relationship_grants_no_access(client, church_payload):
    leader, lead, slug = await _setup(client, church_payload, "fam2")
    a, a_member = await _joined_member(client, slug, leader, "Ann", "ANN00001")
    b, b_member = await _joined_member(client, slug, leader, "Bob", "BOB00002")

    assert (await _relate(client, lead, a_member["id"], b_member["id"], "spouse")).status_code == 201
    for session, other in ((a, b_member), (b, a_member)):
        family = (await client.get("/api/v1/me/family", headers=auth(session["access_token"]))).json()
        assert [r["role"] for r in family["relationships"]] == ["spouse"]
        assert family["dependants"] == []
        denied = await client.get(f"/api/v1/me/family/{other['id']}", headers=auth(session["access_token"]))
        assert denied.status_code == 404

    # One marriage per person, and the same pair can't be recorded twice from the other side.
    again = await _relate(client, lead, b_member["id"], a_member["id"], "spouse")
    assert again.status_code == 409
    third = (await client.post("/api/v1/members", json=adult_member("Cara"), headers=lead)).json()
    assert (await _relate(client, lead, third["id"], a_member["id"], "spouse")).status_code == 409


async def test_access_follows_the_child_being_under_eighteen(client, church_payload):
    leader, lead, slug = await _setup(client, church_payload, "fam3")
    dad, dad_member = await _joined_member(client, slug, leader, "Dan", "DAN00001")
    grown = (
        await client.post(
            "/api/v1/members", json=adult_member("Gary", "Grown", date_of_birth="2000-01-01"), headers=lead
        )
    ).json()
    unknown_age = (await client.post("/api/v1/members", json=adult_member("Nodob", "Unknown"), headers=lead)).json()

    assert (await _relate(client, lead, grown["id"], dad_member["id"], "parent")).status_code == 201
    assert (await _relate(client, lead, unknown_age["id"], dad_member["id"], "guardian")).status_code == 201

    family = (await client.get("/api/v1/me/family", headers=auth(dad["access_token"]))).json()
    assert len(family["relationships"]) == 2  # both recorded
    assert family["dependants"] == []  # neither grants anything: one is an adult, one has no known age
    for member in (grown, unknown_age):
        assert (
            await client.get(f"/api/v1/me/family/{member['id']}", headers=auth(dad["access_token"]))
        ).status_code == 404


async def test_ending_a_relationship_ends_the_access_and_keeps_the_history(client, church_payload):
    leader, lead, slug = await _setup(client, church_payload, "fam4")
    mum, mum_member = await _joined_member(client, slug, leader, "Mia", "MIA00001")
    kid = (await client.post("/api/v1/members", json=_child("Lia"), headers=lead)).json()
    rel = (await _relate(client, lead, kid["id"], mum_member["id"], "guardian")).json()[0]

    assert (await client.get(f"/api/v1/me/family/{kid['id']}", headers=auth(mum["access_token"]))).status_code == 200
    gone = await client.delete(f"/api/v1/members/{kid['id']}/relationships/{rel['id']}", headers=lead)
    assert gone.status_code == 204
    assert (await client.get(f"/api/v1/me/family/{kid['id']}", headers=auth(mum["access_token"]))).status_code == 404
    assert (
        await client.delete(f"/api/v1/members/{kid['id']}/relationships/{rel['id']}", headers=lead)
    ).status_code == 409

    history = (await client.get(f"/api/v1/members/{kid['id']}/relationships", headers=lead)).json()
    assert len(history) == 1 and history[0]["ended_on"] is not None and history[0]["grants_access"] is False


async def test_the_record_cannot_be_made_nonsensical(client, church_payload):
    leader, lead, slug = await _setup(client, church_payload, "fam5")
    adult = (
        await client.post(
            "/api/v1/members", json=adult_member("Pat", "Parent", date_of_birth="1980-01-01"), headers=lead
        )
    ).json()
    teen = (await client.post("/api/v1/members", json=_child("Teen", years=15), headers=lead)).json()
    kid = (await client.post("/api/v1/members", json=_child("Kid", years=6), headers=lead)).json()

    assert (await _relate(client, lead, adult["id"], adult["id"], "parent")).status_code == 409  # self
    assert (
        await _relate(client, lead, kid["id"], teen["id"], "parent")
    ).status_code == 409  # a minor can't be a parent
    assert (await _relate(client, lead, kid["id"], teen["id"], "spouse")).status_code == 409  # nor a spouse

    assert (await _relate(client, lead, kid["id"], adult["id"], "parent")).status_code == 201
    assert (await _relate(client, lead, kid["id"], adult["id"], "parent")).status_code == 409  # duplicate
    assert (
        await _relate(client, lead, adult["id"], kid["id"], "child")
    ).status_code == 409  # same fact, from the other side
    assert (
        await _relate(client, lead, adult["id"], kid["id"], "parent")
    ).status_code == 409  # a child can't be the parent
    assert (await _relate(client, lead, kid["id"], "not-a-uuid", "parent")).status_code in (404, 422)


async def test_relationships_do_not_cross_churches_or_skip_permissions(client, church_payload):
    leader, lead, slug = await _setup(client, church_payload, "fam6")
    _, other_lead, _ = await _setup(client, church_payload, "fam6b")
    mine = (await client.post("/api/v1/members", json=adult_member("Local"), headers=lead)).json()
    foreign = (await client.post("/api/v1/members", json=adult_member("Foreign"), headers=other_lead)).json()

    # Another church's person doesn't exist as far as this church can tell.
    assert (await _relate(client, lead, mine["id"], foreign["id"], "spouse")).status_code == 404
    assert (await client.get(f"/api/v1/members/{foreign['id']}/relationships", headers=lead)).status_code == 404

    # A plain member can't record or read anyone's family through the staff routes.
    joined, joined_member = await _joined_member(client, slug, leader, "Jo", "JO000001")
    headers = auth(joined["access_token"])
    assert (await client.get(f"/api/v1/members/{mine['id']}/relationships", headers=headers)).status_code == 403
    assert (await _relate(client, headers, mine["id"], joined_member["id"], "spouse")).status_code == 403
