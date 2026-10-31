"""Forms module: standalone forms and the registration forms events keep in it.

Covers who may do what (permissions, branch scope, other churches), the public link
(only published forms, only their questions, rate limited, a receipt and nothing more),
server-side validation, history that later edits can't rewrite, the CSV export, and the
event integration (same URLs and data as before, forms reached only through the event).
"""

import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

from app.core.database import platform_session, tenant_session
from app.core.rate_limit import limiter
from app.modules.rbac.models import SYSTEM_ROLE_STAFF_ID
from tests.conftest import accept_invite, auth, unique_email
from tests.test_features import _church, _joined_member
from tests.test_notifications_triggers import _inbox

BASE = "/api/v1"

QUESTIONS = [
    {"label": "T-shirt size", "field_type": "single_choice", "options": ["S", "M", "L"], "required": True},
    {"label": "Dietary notes", "field_type": "text", "required": False},
    {"label": "Allergies", "field_type": "checkbox", "options": ["Nuts", "Dairy", "Gluten"], "required": False},
]


async def _setup(client, church_payload, label):
    payload, session, lead = await _church(client, church_payload, label)
    slug = (await client.get(f"{BASE}/org/profile", headers=lead)).json()["slug"]
    return session, lead, slug


async def _form(client, lead, *, title="Visitor card", fields=None, publish=False, **extra) -> dict:
    created = await client.post(
        f"{BASE}/forms", json={"title": title, "fields": QUESTIONS if fields is None else fields, **extra}, headers=lead
    )
    assert created.status_code == 201, created.text
    form = created.json()
    if publish:
        opened = await client.post(f"{BASE}/forms/{form['id']}/publish", headers=lead)
        assert opened.status_code == 200, opened.text
        form = opened.json()
    return form


def _public(slug, form) -> str:
    return f"{BASE}/churches/{slug}/forms/{form['public_key']}"


async def _submit(client, slug, form, *, name="Casey Visitor", answers=None, **extra):
    body = {"respondent_name": name, "answers": {"T-shirt size": "M"} if answers is None else answers, **extra}
    return await client.post(_public(slug, form) + "/submit", json=body)


async def _staff(client, lead, *, unit_scope=None):
    invite = await client.post(
        f"{BASE}/team/invite",
        json={
            "email": unique_email("forms-staff"),
            "role_id": SYSTEM_ROLE_STAFF_ID,
            **({"unit_scope": unit_scope} if unit_scope else {}),
        },
        headers=lead,
    )
    assert invite.status_code == 200, invite.text
    session = await accept_invite(client, invite.json()["invite_url"], first="Sam", last="Staff")
    return auth(session["access_token"])


async def _unit(client, lead, name):
    root = (await client.get(f"{BASE}/hierarchy/units", headers=lead)).json()[0]["id"]
    resp = await client.post(
        f"{BASE}/hierarchy/units", json={"name": name, "type": "Branch", "parent_id": root}, headers=lead
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _event(client, lead, *, title="Youth Camp", visibility="public", publish=True) -> dict:
    start = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    created = await client.post(
        f"{BASE}/events",
        json={"title": title, "type": "event", "start_date_time": start.isoformat(), "visibility": visibility},
        headers=lead,
    )
    assert created.status_code == 200, created.text
    event = created.json()
    if publish:
        assert (await client.post(f"{BASE}/events/{event['id']}/publish", headers=lead)).status_code == 200
    return event


async def _audit(tenant_id: str, form_id: str) -> list[tuple[str, dict]]:
    async with platform_session() as db:
        rows = await db.query_raw(
            "select action, metadata from audit_logs where tenant_id = $1::uuid and resource_type = 'form'"
            " and resource_id = $2::uuid order by chain_seq",
            tenant_id,
            form_id,
        )
    return [
        (r["action"], json.loads(r["metadata"]) if isinstance(r["metadata"], str) else r["metadata"] or {})
        for r in rows
    ]


# ───────────────────────────── Definitions ─────────────────────────────


async def test_a_form_is_built_published_closed_and_reopened(client, church_payload):
    session, lead, _ = await _setup(client, church_payload, "forms-life")
    form = await _form(client, lead, description="Tell us about your visit")
    assert form["status"] == "draft" and form["submission_count"] == 0
    assert [f["label"] for f in form["fields"]] == ["T-shirt size", "Dietary notes", "Allergies"]
    assert [f["position"] for f in form["fields"]] == [0, 1, 2]
    assert len(form["public_key"]) >= 16

    renamed = await client.patch(f"{BASE}/forms/{form['id']}", json={"title": "Guest card"}, headers=lead)
    assert renamed.json()["title"] == "Guest card" and renamed.json()["description"] == "Tell us about your visit"

    reordered = await client.put(
        f"{BASE}/forms/{form['id']}/fields", json={"fields": list(reversed(QUESTIONS))}, headers=lead
    )
    assert [f["label"] for f in reordered.json()] == ["Allergies", "Dietary notes", "T-shirt size"]

    # draft -> published -> closed -> published; a draft can't be closed, nothing goes back to draft
    assert (await client.post(f"{BASE}/forms/{form['id']}/close", headers=lead)).status_code == 409
    opened = (await client.post(f"{BASE}/forms/{form['id']}/publish", headers=lead)).json()
    assert opened["status"] == "published" and opened["published_at"]
    again = await client.post(f"{BASE}/forms/{form['id']}/publish", headers=lead)
    assert again.status_code == 200 and again.json()["status"] == "published"  # idempotent
    closed = (await client.post(f"{BASE}/forms/{form['id']}/close", headers=lead)).json()
    assert closed["status"] == "closed" and closed["closed_at"]
    reopened = (await client.post(f"{BASE}/forms/{form['id']}/publish", headers=lead)).json()
    assert reopened["status"] == "published" and reopened["closed_at"] is None

    listed = (await client.get(f"{BASE}/forms", headers=lead)).json()
    assert [f["id"] for f in listed] == [form["id"]]
    assert (await client.get(f"{BASE}/forms?status=closed", headers=lead)).json() == []

    tenant_id = session["active_membership"]["tenant_id"]
    actions = [a for a, _ in await _audit(tenant_id, form["id"])]
    assert actions == [
        "form.created",
        "form.updated",
        "form.fields_changed",
        "form.published",
        "form.closed",
        "form.reopened",
    ]


async def test_a_form_definition_is_validated(client, church_payload):
    _, lead, _ = await _setup(client, church_payload, "forms-shape")
    bad = [
        {"title": "  "},
        {"title": "x", "fields": [{"label": "Pick", "field_type": "single_choice", "options": []}]},
        {"title": "x", "fields": [{"label": "Name", "field_type": "text", "options": ["a"]}]},
        {"title": "x", "fields": [{"label": "Pick", "field_type": "checkbox", "options": ["a", "A"]}]},
        {"title": "x", "fields": [{"label": "Pick", "field_type": "date"}]},
        {"title": "x", "fields": [{"label": "Q", "field_type": "text"}, {"label": " q ", "field_type": "text"}]},
        {"title": "x", "fields": [{"label": "Q", "field_type": "text"}] * 31},
    ]
    for body in bad:
        assert (await client.post(f"{BASE}/forms", json=body, headers=lead)).status_code == 422, body
    form = await _form(client, lead)
    assert (await client.patch(f"{BASE}/forms/{form['id']}", json={}, headers=lead)).status_code == 422
    duplicate = await client.put(
        f"{BASE}/forms/{form['id']}/fields",
        json={"fields": [{"label": "A", "field_type": "text"}, {"label": "a", "field_type": "text"}]},
        headers=lead,
    )
    assert duplicate.status_code == 422


async def test_a_form_with_responses_is_closed_not_deleted(client, church_payload):
    _, lead, slug = await _setup(client, church_payload, "forms-delete")
    empty = await _form(client, lead, title="Scratch")
    assert (await client.delete(f"{BASE}/forms/{empty['id']}", headers=lead)).status_code == 204
    assert (await client.get(f"{BASE}/forms/{empty['id']}", headers=lead)).status_code == 404

    form = await _form(client, lead, publish=True)
    assert (await _submit(client, slug, form)).status_code == 201
    refused = await client.delete(f"{BASE}/forms/{form['id']}", headers=lead)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "has_submissions"
    assert (await client.get(f"{BASE}/forms/{form['id']}", headers=lead)).json()["submission_count"] == 1


# ───────────────────────────── Permissions and isolation ─────────────────────────────


async def test_only_people_with_forms_permissions_reach_forms(client, church_payload):
    session, lead, slug = await _setup(client, church_payload, "forms-perm")
    form = await _form(client, lead, publish=True)
    await _submit(client, slug, form)

    member_session, _ = await _joined_member(client, slug, session, "Mia", f"MIA{uuid.uuid4().hex[:8]}")
    member = auth(member_session["access_token"])
    for method, path, body in (
        ("get", "/forms", None),
        ("post", "/forms", {"title": "x"}),
        ("get", f"/forms/{form['id']}", None),
        ("patch", f"/forms/{form['id']}", {"title": "y"}),
        ("put", f"/forms/{form['id']}/fields", {"fields": []}),
        ("post", f"/forms/{form['id']}/close", None),
        ("delete", f"/forms/{form['id']}", None),
        ("get", f"/forms/{form['id']}/submissions", None),
        ("get", f"/forms/{form['id']}/submissions/export", None),
    ):
        resp = await client.request(method, BASE + path, json=body, headers=member)
        assert resp.status_code == 403, (method, path, resp.status_code)
        anonymous = await client.request(method, BASE + path, json=body)
        assert anonymous.status_code == 401, (method, path)

    staff = await _staff(client, lead)  # staff hold all three forms permissions
    assert (await client.get(f"{BASE}/forms/{form['id']}/submissions", headers=staff)).status_code == 200
    assert (await _form(client, staff, title="Staff made this")) is not None


async def test_forms_and_responses_are_private_to_their_church(client, church_payload):
    session, lead, slug = await _setup(client, church_payload, "forms-a")
    other_session, other, other_slug = await _setup(client, church_payload, "forms-b")
    mine = await _form(client, lead, publish=True)
    sent = await _submit(client, slug, mine)
    sub_id = sent.json()["id"]
    theirs = await _form(client, other, title="Theirs", publish=True)

    assert [f["title"] for f in (await client.get(f"{BASE}/forms", headers=other)).json()] == ["Theirs"]
    for path in (
        f"/forms/{mine['id']}",
        f"/forms/{mine['id']}/submissions",
        f"/forms/{mine['id']}/submissions/export",
        f"/forms/{mine['id']}/submissions/{sub_id}",
    ):
        assert (await client.get(BASE + path, headers=other)).status_code == 404, path
    for method, path, body in (
        ("patch", f"/forms/{mine['id']}", {"title": "Hijacked"}),
        ("put", f"/forms/{mine['id']}/fields", {"fields": []}),
        ("post", f"/forms/{mine['id']}/close", None),
        ("delete", f"/forms/{mine['id']}", None),
    ):
        assert (await client.request(method, BASE + path, json=body, headers=other)).status_code == 404, path
    assert (await client.get(f"{BASE}/forms/{mine['id']}", headers=lead)).json()["title"] == "Visitor card"

    # A key only works under its own church's address.
    assert (await client.get(f"{BASE}/churches/{other_slug}/forms/{mine['public_key']}")).status_code == 404
    assert (await _submit(client, other_slug, mine)).status_code == 404

    # And underneath the API: the app role sees only its own church's rows, whatever the query.
    other_tenant = other_session["active_membership"]["tenant_id"]
    async with tenant_session(other_tenant) as db:
        assert [r["title"] for r in await db.query_raw("select title from forms")] == ["Theirs"]
        assert await db.query_raw("select 1 from form_submissions") == []
        assert await db.query_raw("select 1 from form_fields where form_id = $1::uuid", mine["id"]) == []
    assert theirs["id"] != mine["id"]


async def test_a_branch_session_reaches_its_own_branch_and_reads_church_wide_forms(client, church_payload):
    _, lead, slug = await _setup(client, church_payload, "forms-branch")
    east, west = await _unit(client, lead, "East"), await _unit(client, lead, "West")
    east_staff = await _staff(client, lead, unit_scope=east)

    church_wide = await _form(client, lead, title="Church-wide", publish=True)
    west_form = await _form(client, lead, title="West", unit_id=west, publish=True)
    mine = await _form(client, east_staff, title="East")  # lands in their own branch
    assert mine["unit_id"] == east

    visible = {f["title"] for f in (await client.get(f"{BASE}/forms", headers=east_staff)).json()}
    assert visible == {"Church-wide", "East"}
    assert (await client.get(f"{BASE}/forms/{church_wide['id']}", headers=east_staff)).status_code == 200
    assert (await client.get(f"{BASE}/forms/{west_form['id']}", headers=east_staff)).status_code == 404
    assert (await client.get(f"{BASE}/forms/{west_form['id']}/submissions", headers=east_staff)).status_code == 404

    # Changing a church-wide form needs a whole-church session; so does placing one elsewhere.
    refused = await client.patch(f"{BASE}/forms/{church_wide['id']}", json={"title": "Mine now"}, headers=east_staff)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "out_of_scope"
    elsewhere = await client.post(f"{BASE}/forms", json={"title": "x", "unit_id": west}, headers=east_staff)
    assert elsewhere.status_code == 403
    assert (
        await client.patch(f"{BASE}/forms/{mine['id']}", json={"title": "East 2"}, headers=east_staff)
    ).status_code == 200

    # The branch form's responses are readable by the branch; the public link still works.
    assert (await _submit(client, slug, west_form)).status_code == 201
    assert (await client.get(f"{BASE}/forms/{west_form['id']}/submissions", headers=lead)).json()[0]["respondent_name"]


# ───────────────────────────── The public link ─────────────────────────────


async def test_the_public_link_shows_a_published_form_and_nothing_else(client, church_payload):
    _, lead, slug = await _setup(client, church_payload, "forms-public")
    form = await _form(client, lead, description="Hello")

    assert (await client.get(_public(slug, form))).status_code == 404  # a draft isn't public
    assert (await _submit(client, slug, form)).status_code == 404
    assert (await client.get(f"{BASE}/churches/{slug}/forms/{uuid.uuid4().hex}")).status_code == 404

    await client.post(f"{BASE}/forms/{form['id']}/publish", headers=lead)
    page = (await client.get(_public(slug, form))).json()
    assert set(page) == {"title", "description", "status", "fields"}
    assert page["status"] == "published" and [f["label"] for f in page["fields"]] == [f["label"] for f in QUESTIONS]
    assert form["public_key"] not in json.dumps(page) and form["id"] not in json.dumps(page)

    await client.post(f"{BASE}/forms/{form['id']}/close", headers=lead)
    assert (await client.get(_public(slug, form))).json()["status"] == "closed"
    closed = await _submit(client, slug, form)
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "form_closed"


async def test_a_submission_is_checked_on_the_server(client, church_payload):
    _, lead, slug = await _setup(client, church_payload, "forms-validate")
    form = await _form(client, lead, publish=True)
    cases = [
        ({"T-shirt size": ""}, "answer_required"),
        ({}, "answer_required"),
        ({"T-shirt size": "XXL"}, "answer_invalid"),
        ({"T-shirt size": ["M"]}, "answer_invalid"),
        ({"T-shirt size": "M", "Allergies": "Nuts"}, "answer_invalid"),
        ({"T-shirt size": "M", "Allergies": ["Nuts", "Pollen"]}, "answer_invalid"),
        ({"T-shirt size": "M", "Dietary notes": ["a"]}, "answer_invalid"),
        ({"T-shirt size": "M", "Favourite colour": "blue"}, "unknown_question"),
    ]
    for answers, code in cases:
        limiter.reset()  # this test is about validation, not the five-a-minute limit
        resp = await _submit(client, slug, form, answers=answers)
        assert resp.status_code == 400 and resp.json()["error"]["code"] == code, (answers, resp.text)
    limiter.reset()
    for extra in (
        {"respondent_email": "not-an-email"},
        {"respondent_phone": "call me"},
        {"answers": {"Dietary notes": "x" * 2001, "T-shirt size": "M"}},
    ):
        assert (await _submit(client, slug, form, **extra)).status_code == 422, extra
    limiter.reset()
    assert (await client.post(_public(slug, form) + "/submit", json={"answers": {}})).status_code == 422  # no name
    assert (await client.get(f"{BASE}/forms/{form['id']}", headers=lead)).json()["submission_count"] == 0

    limiter.reset()
    ok = await _submit(
        client,
        slug,
        form,
        answers={"T-shirt size": "L", "Dietary notes": "  vegetarian  ", "Allergies": ["Nuts", "Nuts", "Dairy"]},
        respondent_email="casey@example.com",
        respondent_phone="+250 788 123 456",
    )
    assert ok.status_code == 201, ok.text
    # The caller gets a receipt, never the response back.
    assert set(ok.json()) == {"id", "created_at"}
    saved = (await client.get(f"{BASE}/forms/{form['id']}/submissions", headers=lead)).json()[0]
    assert saved["answers"] == {"T-shirt size": "L", "Dietary notes": "vegetarian", "Allergies": ["Nuts", "Dairy"]}
    assert saved["respondent_email"] == "casey@example.com"


async def test_the_public_link_is_rate_limited(client, church_payload):
    _, lead, slug = await _setup(client, church_payload, "forms-rate")
    form = await _form(client, lead, publish=True)
    codes = [(await _submit(client, slug, form)).status_code for _ in range(6)]
    assert codes == [201] * 5 + [429]  # five a minute
    assert (await client.get(f"{BASE}/forms/{form['id']}", headers=lead)).json()["submission_count"] == 5


# ───────────────────────────── History ─────────────────────────────


async def test_editing_a_form_never_changes_what_was_collected(client, church_payload):
    _, lead, slug = await _setup(client, church_payload, "forms-history")
    form = await _form(client, lead, title="Camp", publish=True)
    sent = await _submit(
        client, slug, form, answers={"T-shirt size": "M", "Dietary notes": "none", "Allergies": ["Nuts"]}
    )
    sub_id = sent.json()["id"]

    await client.patch(f"{BASE}/forms/{form['id']}", json={"title": "Camp 2027"}, headers=lead)
    await client.put(
        f"{BASE}/forms/{form['id']}/fields",
        json={
            "fields": [
                {"label": "Shirt", "field_type": "single_choice", "options": ["XL"], "required": True},
                {"label": "Allergies", "field_type": "text"},
            ]
        },
        headers=lead,
    )
    later = await _submit(client, slug, form, answers={"Shirt": "XL"})
    assert later.status_code == 201

    old = (await client.get(f"{BASE}/forms/{form['id']}/submissions/{sub_id}", headers=lead)).json()
    assert old["answers"] == {"T-shirt size": "M", "Dietary notes": "none", "Allergies": ["Nuts"]}
    snap = old["form_snapshot"]
    assert snap["form_title"] == "Camp" and snap["reconstructed"] is False
    assert [(f["label"], f["field_type"], f["options"], f["required"]) for f in snap["fields"]] == [
        ("T-shirt size", "single_choice", ["S", "M", "L"], True),
        ("Dietary notes", "text", [], False),
        ("Allergies", "checkbox", ["Nuts", "Dairy", "Gluten"], False),
    ]
    new_id = later.json()["id"]
    new = (await client.get(f"{BASE}/forms/{form['id']}/submissions/{new_id}", headers=lead)).json()
    assert new["form_snapshot"]["form_title"] == "Camp 2027"
    assert [f["label"] for f in new["form_snapshot"]["fields"]] == ["Shirt", "Allergies"]


async def test_a_submission_cannot_be_changed_or_deleted_even_by_the_app(client, church_payload):
    session, lead, slug = await _setup(client, church_payload, "forms-immutable")
    form = await _form(client, lead, publish=True)
    sub_id = (await _submit(client, slug, form)).json()["id"]
    tenant_id = session["active_membership"]["tenant_id"]

    async def attempt(sql: str) -> str:
        try:
            async with tenant_session(tenant_id) as db:
                await db.execute_raw(sql, sub_id)
        except Exception as exc:  # noqa: BLE001 - what matters is that it is refused
            return str(exc)
        return ""

    assert (
        "permission denied"
        in (await attempt("update form_submissions set respondent_name = 'x' where id = $1::uuid")).lower()
    )
    assert "permission denied" in (await attempt("delete from form_submissions where id = $1::uuid")).lower()
    # The platform role can't rewrite one either (the trigger, not just the grants).
    try:
        async with platform_session() as db:
            await db.execute_raw("update form_submissions set answers = '{}' where id = $1::uuid", sub_id)
        refused = False
    except Exception:  # noqa: BLE001
        refused = True
    assert refused
    saved = (await client.get(f"{BASE}/forms/{form['id']}/submissions/{sub_id}", headers=lead)).json()
    assert saved["respondent_name"] == "Casey Visitor"


# ───────────────────────────── CSV ─────────────────────────────


def _rows(resp) -> list[list[str]]:
    assert resp.content.startswith(b"\xef\xbb\xbf")  # BOM, so Excel reads UTF-8
    return list(csv.reader(io.StringIO(resp.content.decode("utf-8-sig"))))


async def test_responses_export_as_csv_and_are_audited(client, church_payload):
    session, lead, slug = await _setup(client, church_payload, "forms-csv")
    form = await _form(client, lead, title="Camp: Sign-up!", publish=True)
    await _submit(client, slug, form, name="Zoë Álvarez", answers={"T-shirt size": "S", "Allergies": ["Nuts", "Dairy"]})
    await _submit(
        client,
        slug,
        form,
        name='=HYPERLINK("http://evil.example","click")',
        answers={"T-shirt size": "L", "Dietary notes": '@SUM(1+1), "quoted"\nline two'},
        respondent_email="x@example.com",
        respondent_phone="+250788123456",
    )
    resp = await client.get(f"{BASE}/forms/{form['id']}/submissions/export", headers=lead)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"].startswith('attachment; filename="camp-sign-up-responses-')
    assert resp.headers["cache-control"] == "no-store"

    rows = _rows(resp)
    assert rows[0] == ["Name", "Email", "Phone", "Submitted", "T-shirt size", "Dietary notes", "Allergies"]
    newest, oldest = rows[1], rows[2]
    assert oldest[0] == "Zoë Álvarez" and oldest[4:] == ["S", "", "Nuts, Dairy"]
    # Typed on a public page, so nothing may be read by a spreadsheet as a formula.
    assert newest[0].startswith("'=HYPERLINK") and newest[5].startswith("'@SUM")
    assert "quoted" in newest[5] and "line two" in newest[5]
    assert newest[2] == "+250788123456"  # a phone number stays a phone number
    assert len(newest[3]) == 16  # "2026-10-31 09:30"

    # A question removed later keeps its column (its answers were kept).
    await client.put(
        f"{BASE}/forms/{form['id']}/fields", json={"fields": [{"label": "Shirt", "field_type": "text"}]}, headers=lead
    )
    assert _rows(await client.get(f"{BASE}/forms/{form['id']}/submissions/export", headers=lead))[0] == [
        "Name",
        "Email",
        "Phone",
        "Submitted",
        "Shirt",
        "T-shirt size",
        "Dietary notes",
        "Allergies",
    ]

    # The export is on the record, with a count and no personal data.
    tenant_id = session["active_membership"]["tenant_id"]
    exports = [m for a, m in await _audit(tenant_id, form["id"]) if a == "form.submissions_exported"]
    assert [m["count"] for m in exports] == [2, 2]
    assert "Zoë" not in json.dumps(exports) and "example.com" not in json.dumps(exports)


async def test_reading_one_response_is_audited(client, church_payload):
    session, lead, slug = await _setup(client, church_payload, "forms-read")
    form = await _form(client, lead, publish=True)
    sub_id = (await _submit(client, slug, form)).json()["id"]
    assert (await client.get(f"{BASE}/forms/{form['id']}/submissions/{sub_id}", headers=lead)).status_code == 200
    assert (await client.get(f"{BASE}/forms/{form['id']}/submissions/{uuid.uuid4()}", headers=lead)).status_code == 404
    tenant_id = session["active_membership"]["tenant_id"]
    reads = [m for a, m in await _audit(tenant_id, form["id"]) if a == "form.submission_read"]
    assert reads == [{"submission_id": sub_id}]


# ───────────────────────────── Notifications ─────────────────────────────


async def test_the_form_owner_hears_about_a_response_once_a_day_without_the_answers(client, church_payload):
    _, lead, slug = await _setup(client, church_payload, "forms-notify")
    form = await _form(client, lead, title="Guest card", publish=True)
    for i in range(3):
        assert (await _submit(client, slug, form, name=f"Secret Person {i}")).status_code == 201
    mine = [n for n in await _inbox(client, lead) if n["title"].startswith("New response")]
    assert len(mine) == 1  # three responses, one nudge
    assert mine[0]["title"] == "New response: Guest card"
    assert "Secret" not in json.dumps(mine[0]) and "Casey" not in json.dumps(mine[0])
    assert mine[0]["action_url"] == f"/staff/forms/{form['id']}/responses"


# ───────────────────────────── Events keep working, through Forms ─────────────────────────────


async def test_event_registration_keeps_its_urls_and_data(client, church_payload):
    session, lead, slug = await _setup(client, church_payload, "forms-event")
    event = await _event(client, lead)
    base = f"{BASE}/events/{event['id']}"
    assert (await client.get(f"{base}/fields", headers=lead)).json() == []

    saved = await client.put(f"{base}/fields", json={"fields": QUESTIONS}, headers=lead)
    assert saved.status_code == 200 and [f["label"] for f in saved.json()] == [f["label"] for f in QUESTIONS]
    assert set(saved.json()[0]) == {"id", "label", "field_type", "options", "required", "position"}

    detail = (await client.get(f"{BASE}/churches/{slug}/events/{event['id']}")).json()
    assert [f["label"] for f in detail["registration_fields"]] == [f["label"] for f in QUESTIONS]
    assert detail["registration_open"] is True

    register = f"{BASE}/churches/{slug}/events/{event['id']}/register"
    assert (await client.post(register, json={"respondent_name": "Casey", "answers": {}})).status_code == 400
    ok = await client.post(
        register,
        json={"respondent_name": "Casey", "respondent_email": "casey@example.com", "answers": {"T-shirt size": "M"}},
    )
    assert ok.status_code == 201
    assert set(ok.json()) == {"id", "respondent_name", "respondent_email", "respondent_phone", "answers", "created_at"}

    responses = (await client.get(f"{base}/responses", headers=lead)).json()
    assert [(r["respondent_name"], r["answers"]) for r in responses] == [("Casey", {"T-shirt size": "M"})]
    assert (await client.get(f"{base}/responses", headers=lead)).headers["x-total-count"] == "1"

    # The event's form isn't a standalone form: not listed, not reachable by id, not by public key.
    assert (await client.get(f"{BASE}/forms", headers=lead)).json() == []
    async with platform_session() as db:
        row = (
            await db.query_raw(
                "select id, public_key from forms where owner_kind = 'event' and owner_id = $1::uuid", event["id"]
            )
        )[0]
    assert (await client.get(f"{BASE}/forms/{row['id']}", headers=lead)).status_code == 404
    assert (await client.get(f"{BASE}/forms/{row['id']}/submissions", headers=lead)).status_code == 404
    assert (await client.get(f"{BASE}/churches/{slug}/forms/{row['public_key']}")).status_code == 404

    # The event's author is told, with a pointer and no answers.
    notes = [n for n in await _inbox(client, lead) if n["title"].startswith("New response")]
    assert len(notes) == 1 and notes[0]["type"] == "event"
    assert notes[0]["action_url"] == f"/staff/events/{event['id']}/responses"
    assert "Casey" not in json.dumps(notes[0])

    tenant_id = session["active_membership"]["tenant_id"]
    actions = [(a, m) for a, m in await _audit(tenant_id, str(row["id"]))]
    assert actions[0][0] == "form.fields_changed" and actions[0][1]["owner_kind"] == "event"


async def test_event_responses_are_for_organisers_and_the_event_branch(client, church_payload):
    session, lead, slug = await _setup(client, church_payload, "forms-event-perm")
    event = await _event(client, lead)
    base = f"{BASE}/events/{event['id']}"
    await client.post(f"{BASE}/churches/{slug}/events/{event['id']}/register", json={"respondent_name": "Ann"})

    member_session, _ = await _joined_member(client, slug, session, "Mia", f"MIA{uuid.uuid4().hex[:8]}")
    member = auth(member_session["access_token"])
    for method, path, body in (
        ("put", "/fields", {"fields": []}),
        ("get", "/responses", None),
        ("get", "/responses/export", None),
        ("put", "/registration", {"status": "closed"}),
    ):
        assert (await client.request(method, base + path, json=body, headers=member)).status_code == 403, path

    # Another church's organiser can't see this event at all.
    _, other, _ = await _setup(client, church_payload, "forms-event-other")
    for method, path, body in (
        ("get", "/fields", None),
        ("put", "/fields", {"fields": []}),
        ("get", "/responses", None),
        ("get", "/responses/export", None),
        ("put", "/registration", {"status": "closed"}),
    ):
        assert (await client.request(method, base + path, json=body, headers=other)).status_code == 404, path

    # A branch organiser can't touch a church-wide event's form.
    east = await _unit(client, lead, "East")
    east_staff = await _staff(client, lead, unit_scope=east)
    refused = await client.put(f"{base}/fields", json={"fields": []}, headers=east_staff)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "out_of_scope"
    assert (await client.get(f"{base}/responses/export", headers=east_staff)).status_code == 403


async def test_closing_event_registration_stops_sign_ups_and_keeps_responses(client, church_payload):
    _, lead, slug = await _setup(client, church_payload, "forms-event-close")
    event = await _event(client, lead)
    base = f"{BASE}/events/{event['id']}"
    register = f"{BASE}/churches/{slug}/events/{event['id']}/register"
    assert (await client.get(f"{base}/registration", headers=lead)).json() == {"status": "published"}
    assert (await client.post(register, json={"respondent_name": "Early"})).status_code == 201

    assert (await client.put(f"{base}/registration", json={"status": "closed"}, headers=lead)).json() == {
        "status": "closed"
    }
    assert (await client.get(f"{BASE}/churches/{slug}/events/{event['id']}")).json()["registration_open"] is False
    closed = await client.post(register, json={"respondent_name": "Late"})
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "registration_closed"
    assert [r["respondent_name"] for r in (await client.get(f"{base}/responses", headers=lead)).json()] == ["Early"]

    assert (await client.put(f"{base}/registration", json={"status": "published"}, headers=lead)).status_code == 200
    assert (await client.post(register, json={"respondent_name": "Back"})).status_code == 201
    assert (await client.put(f"{base}/registration", json={"status": "draft"}, headers=lead)).status_code == 422


async def test_event_sign_ups_are_only_for_published_public_events_and_rate_limited(client, church_payload):
    _, lead, slug = await _setup(client, church_payload, "forms-event-public")
    draft = await _event(client, lead, title="Draft", publish=False)
    members_only = await _event(client, lead, title="Members", visibility="members")
    for event in (draft, members_only):
        resp = await client.post(
            f"{BASE}/churches/{slug}/events/{event['id']}/register", json={"respondent_name": "Nope"}
        )
        assert resp.status_code == 404
        assert (await client.get(f"{BASE}/events/{event['id']}/responses", headers=lead)).json() == []

    event = await _event(client, lead, title="Open")
    register = f"{BASE}/churches/{slug}/events/{event['id']}/register"
    limiter.reset()  # the two refused sign-ups above counted too
    codes = [(await client.post(register, json={"respondent_name": f"P{i}"})).status_code for i in range(21)]
    assert codes == [201] * 20 + [429]  # twenty an hour


async def test_event_responses_export_as_csv_with_the_same_rows(client, church_payload):
    session, lead, slug = await _setup(client, church_payload, "forms-event-csv")
    event = await _event(client, lead, title="Youth Camp")
    base = f"{BASE}/events/{event['id']}"
    empty = await client.get(f"{base}/responses/export", headers=lead)
    assert empty.status_code == 200 and _rows(empty) == [["Name", "Email", "Phone", "Submitted"]]

    await client.put(f"{base}/fields", json={"fields": QUESTIONS}, headers=lead)
    register = f"{BASE}/churches/{slug}/events/{event['id']}/register"
    await client.post(register, json={"respondent_name": "Casey", "answers": {"T-shirt size": "M"}})
    resp = await client.get(f"{base}/responses/export", headers=lead)
    assert resp.headers["content-disposition"].startswith('attachment; filename="youth-camp-responses-')
    rows = _rows(resp)
    assert rows[0] == ["Name", "Email", "Phone", "Submitted", "T-shirt size", "Dietary notes", "Allergies"]
    assert rows[1][0] == "Casey" and rows[1][4:] == ["M", "", ""]


async def test_registrants_are_still_reminded_by_the_email_they_signed_up_with(client, church_payload):
    """The reminder job learns who signed up from the Forms service, not from its tables."""
    from app.modules.forms import service as forms_service

    _, lead, slug = await _setup(client, church_payload, "forms-event-emails")
    event = await _event(client, lead)
    register = f"{BASE}/churches/{slug}/events/{event['id']}/register"
    await client.post(register, json={"respondent_name": "A", "respondent_email": "A@Example.com"})
    await client.post(register, json={"respondent_name": "B", "respondent_email": "a@example.com"})
    await client.post(register, json={"respondent_name": "C"})
    async with platform_session() as db:
        assert await forms_service.owned_respondent_emails(db, "event", event["id"]) == ["a@example.com"]
