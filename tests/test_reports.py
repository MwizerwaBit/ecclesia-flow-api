"""Reports: they count what the caller may see, filter by date, export as CSV,
and answer 403 when the module or the permission isn't there."""

import uuid
from datetime import date

from tests.conftest import auth


def _phone() -> str:
    return f"+25078{uuid.uuid4().int % 10**7:07d}"


async def _church(client, church_payload, label):
    resp = await client.post("/api/v1/auth/register", json=church_payload(label))
    assert resp.status_code == 200, resp.text
    return auth(resp.json()["access_token"])


async def _visitor(client, h, first, joined=None):
    body = {
        "first_name": first,
        "last_name": "Test",
        "gender": "female",
        "phone": _phone(),
        "status": "visitor",
        "consent": {"given_by": "self"},
    }
    if joined:
        body["joined_at"] = joined
    resp = await client.post("/api/v1/members", json=body, headers=h)
    assert resp.status_code == 200, resp.text


async def test_report_list_run_and_export(client, church_payload):
    h = await _church(client, church_payload, "reports")
    listed = (await client.get("/api/v1/reports", headers=h)).json()
    assert {"membership_by_status", "new_people_by_month"} <= {r["key"] for r in listed}

    empty = (await client.get("/api/v1/reports/membership_by_status", headers=h)).json()
    assert [r["people"] for r in empty["rows"]] == [0, 0, 0, 0]  # an empty church still gets the table

    await _visitor(client, h, "Aline", joined="2025-03-10")
    await _visitor(client, h, "Beata", joined="2025-03-20")
    await _visitor(client, h, "Chantal", joined="2025-05-01")

    by_status = (await client.get("/api/v1/reports/membership_by_status", headers=h)).json()
    visitors = next(r for r in by_status["rows"] if r["status"] == "visitor")
    assert visitors["people"] == 3 and visitors["share"] == 1

    monthly = (await client.get("/api/v1/reports/new_people_by_month?from=2025-02-01&to=2025-06-30", headers=h)).json()
    assert [(r["month"], r["people"]) for r in monthly["rows"]] == [
        ("2025-02", 0),
        ("2025-03", 2),
        ("2025-04", 0),
        ("2025-05", 1),
        ("2025-06", 0),
    ]
    assert monthly["filters"] == {"from": "2025-02-01", "to": "2025-06-30"}

    csv_file = await client.get(
        "/api/v1/reports/new_people_by_month/export.csv?from=2025-02-01&to=2025-03-31", headers=h
    )
    assert csv_file.status_code == 200 and "attachment" in csv_file.headers["content-disposition"]
    assert csv_file.content.decode("utf-8-sig").splitlines() == ["Month,New people", "2025-02,0", "2025-03,2"]

    default_range = (await client.get("/api/v1/reports/new_people_by_month", headers=h)).json()
    assert default_range["rows"][-1]["month"] == date.today().strftime("%Y-%m") and len(default_range["rows"]) == 13


async def test_bad_filters_and_unknown_reports(client, church_payload):
    h = await _church(client, church_payload, "reports-bad")
    backwards = await client.get("/api/v1/reports/new_people_by_month?from=2025-06-01&to=2025-01-01", headers=h)
    assert backwards.status_code == 422 and backwards.json()["error"]["code"] == "invalid_filter"
    garbage = await client.get("/api/v1/reports/new_people_by_month?from=yesterday", headers=h)
    assert garbage.status_code == 422
    too_long = await client.get("/api/v1/reports/new_people_by_month?from=1990-01-01", headers=h)
    assert too_long.status_code == 422
    assert (await client.get("/api/v1/reports/nothing", headers=h)).status_code == 404


async def test_reports_are_per_church_and_need_the_module_and_permission(client, church_payload):
    mine = await _church(client, church_payload, "reports-mine")
    other = await _church(client, church_payload, "reports-other")
    await _visitor(client, mine, "Aline")
    theirs = (await client.get("/api/v1/reports/membership_by_status", headers=other)).json()
    assert sum(r["people"] for r in theirs["rows"]) == 0

    assert (await client.get("/api/v1/reports")).status_code == 401


async def test_staff_without_analytics_permission_are_refused(client, church_payload):
    from tests.test_foundation import _scoped_staff

    h = await _church(client, church_payload, "reports-staff")
    root = (await client.get("/api/v1/hierarchy/units", headers=h)).json()[0]["id"]
    staff = await _scoped_staff(client, h, root)
    assert (await client.get("/api/v1/reports", headers=staff)).status_code == 403
