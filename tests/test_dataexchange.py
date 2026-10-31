"""Spreadsheet export and import of members.

The promises: an empty church still exports a file in the import's shape, a
check run saves nothing, a bad file never half-lands, the same file twice does
not create everyone twice, and one church never sees another's people.
"""

import csv
import io
import uuid

from tests.conftest import auth
from tests.test_foundation import _mfa

HEADER = "first_name,last_name,gender,status,phone,national_id,household_name,groups,envelope_number,date_of_birth"


def _phone() -> str:
    return f"+25078{uuid.uuid4().int % 10**7:07d}"


def _national_id() -> str:
    return f"1{uuid.uuid4().int % 10**15:015d}"


def _file(*rows: str, header: str = HEADER) -> dict:
    body = "\r\n".join([header, *rows]) + "\r\n"
    return {"file": ("people.csv", body.encode("utf-8"), "text/csv")}


async def _church(client, church_payload, label):
    payload = church_payload(label)
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200, resp.text
    session = await _mfa(client, payload, resp.json())  # exporting people needs a two-step-verified session
    return auth(session["access_token"])


async def _count(client, h) -> int:
    return len((await client.get("/api/v1/members", headers=h)).json())


async def _import(client, h, files, **form):
    return await client.post(
        "/api/v1/data/imports/members", files=files, data={k: str(v).lower() for k, v in form.items()}, headers=h
    )


async def test_template_and_empty_export_are_the_same_header(client, church_payload):
    h = await _church(client, church_payload, "empty")

    datasets = (await client.get("/api/v1/data/datasets", headers=h)).json()
    members = next(d for d in datasets if d["key"] == "members")
    assert members["can_import"] and members["can_export"]
    assert {"first_name", "last_name", "gender"} == {c["key"] for c in members["columns"] if c["required"]}

    template = await client.get("/api/v1/data/templates/members.csv", headers=h)
    export = await client.get("/api/v1/data/exports/members.csv", headers=h)
    assert template.status_code == export.status_code == 200
    assert "attachment" in export.headers["content-disposition"] and export.headers["cache-control"] == "no-store"
    template_lines = template.content.decode("utf-8-sig").strip().splitlines()
    assert len(template_lines) == 1  # the header and nothing else
    assert export.content.decode("utf-8-sig").strip().splitlines() == template_lines
    assert template_lines[0].split(",") == [c["key"] for c in members["columns"]]


async def test_signed_out_and_unknown_dataset_are_refused(client, church_payload):
    assert (await client.get("/api/v1/data/templates/members.csv")).status_code == 401
    h = await _church(client, church_payload, "unknown")
    assert (await client.get("/api/v1/data/exports/nothing.csv", headers=h)).status_code == 404


async def test_check_run_saves_nothing_and_explains_every_problem(client, church_payload):
    h = await _church(client, church_payload, "check")
    await client.post("/api/v1/groups", json={"name": "Choir"}, headers=h)
    same = _phone()
    files = _file(
        f"Aline,Uwase,female,visitor,{_phone()},,Uwase family,Choir,,",
        "Brice,Nkusi,male,visitor,,,,,,",  # no phone or email
        f"Chantal,Mutesi,female,visitor,{same},,,,,",
        f"Chantal,Mutesi,female,visitor,{same},,,,,",  # the same person again
        f"Denis,Habimana,male,visitor,{_phone()},,,Nonexistent group,,",
        f"Eric,Ndayisaba,male,active,{_phone()},,,,,",  # an adult member needs an ID number
        f"Fabrice,Gatera,male,visitor,{_phone()},,,,,31/02/2020",  # not a real date
    )
    before = await _count(client, h)
    report = (await _import(client, h, files)).json()

    assert report["dry_run"] is True and report["committed"] is False
    assert report["total_rows"] == 7 and report["valid"] == 2 and report["duplicates"] == 1 and report["failed"] == 4
    by_row = {i["row"]: i for i in report["issues"]}
    assert "phone number or an email" in " ".join(by_row[3]["messages"])
    assert by_row[5]["status"] == "duplicate"
    assert "no group called" in " ".join(by_row[6]["messages"])
    assert "ID number" in " ".join(by_row[7]["messages"])
    assert "date_of_birth" in " ".join(by_row[8]["messages"])
    assert await _count(client, h) == before  # a check saves nothing


async def test_a_bad_file_never_half_lands_and_consent_is_required(client, church_payload):
    h = await _church(client, church_payload, "halfway")
    files = lambda: _file(f"Aline,Uwase,female,visitor,{_phone()},,,,,", "Brice,Nkusi,male,visitor,,,,,,")  # noqa: E731
    before = await _count(client, h)

    no_consent = await _import(client, h, files(), dry_run=False)
    assert no_consent.status_code == 422 and no_consent.json()["error"]["code"] == "consent_required"

    refused = (await _import(client, h, files(), dry_run=False, consent_confirmed=True)).json()
    assert refused["committed"] is False and refused["failed"] == 1
    assert await _count(client, h) == before  # the good row was held back too

    skipped = (await _import(client, h, files(), dry_run=False, consent_confirmed=True, skip_invalid=True)).json()
    assert skipped["committed"] is True and skipped["valid"] == 1 and skipped["failed"] == 1
    assert await _count(client, h) == before + 1


async def test_import_builds_households_and_groups_and_a_rerun_adds_nobody(client, church_payload):
    h = await _church(client, church_payload, "rerun")
    group = (await client.post("/api/v1/groups", json={"name": "Choir"}, headers=h)).json()
    files = lambda: _file(  # noqa: E731
        f"Aline,Uwase,female,visitor,{phones[0]},,Uwase family,Choir,,",
        f"Beata,Uwase,female,visitor,{phones[1]},,Uwase family,,,",
        f"Claude,Mugabo,male,active,{phones[2]},{national_ids[0]},,,0042,1985-03-02",
    )
    phones, national_ids = [_phone(), _phone(), _phone()], [_national_id()]

    done = (await _import(client, h, files(), dry_run=False, consent_confirmed=True)).json()
    assert done["committed"] is True and done["valid"] == 3 and done["failed"] == 0

    people = {m["first_name"]: m for m in (await client.get("/api/v1/members", headers=h)).json()}
    assert people["Aline"]["household_id"] and people["Aline"]["household_id"] == people["Beata"]["household_id"]
    assert people["Claude"]["household_id"] is None and people["Claude"]["envelope_number"] == "0042"
    assert [g["id"] for g in people["Aline"]["groups"]] == [group["id"]]
    detail = (await client.get(f"/api/v1/members/{people['Claude']['id']}", headers=h)).json()
    assert detail["consent"]["given_by"] == "self" and detail["national_id_last4"] == national_ids[0][-4:]

    again = (await _import(client, h, files(), dry_run=False, consent_confirmed=True)).json()
    assert again["valid"] == 0 and again["duplicates"] == 1 + 1 and again["failed"] == 1  # the ID number is taken
    assert await _count(client, h) == 3

    # A household that already exists by name is joined, never duplicated.
    more = _file(f"Dora,Uwase,female,visitor,{_phone()},,Uwase family,,,")
    assert (await _import(client, h, more, dry_run=False, consent_confirmed=True)).json()["committed"] is True
    households = (await client.get("/api/v1/households", headers=h)).json()
    assert [x["name"] for x in households].count("Uwase family") == 1


async def test_an_export_can_be_imported_again_and_is_safe_in_a_spreadsheet(client, church_payload):
    h = await _church(client, church_payload, "roundtrip")
    await client.post("/api/v1/groups", json={"name": "Choir"}, headers=h)
    phone = _phone()
    files = _file(
        f"=HYPERLINK(1),Uwase,female,visitor,{phone},,Uwase family,Choir,,",
        f"Beata,Uwase,female,visitor,{_phone()},,,,,2001-04-05",
    )
    assert (await _import(client, h, files, dry_run=False, consent_confirmed=True)).json()["valid"] == 2

    export = (await client.get("/api/v1/data/exports/members.csv", headers=h)).content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(export)))
    assert len(rows) == 2
    formula = next(r for r in rows if r["last_name"] == "Uwase" and r["household_name"])
    assert formula["first_name"].startswith("'=")  # stored as text, never run as a formula
    assert formula["phone"] == phone and formula["groups"] == "Choir" and formula["national_id"] == ""
    assert next(r for r in rows if r["first_name"] == "Beata")["date_of_birth"] == "2001-04-05"

    # The file we just exported goes straight back in: everyone is already on file.
    back = (
        await client.post(
            "/api/v1/data/imports/members",
            files={"file": ("export.csv", export.encode("utf-8"), "text/csv")},
            data={"dry_run": "true"},
            headers=h,
        )
    ).json()
    assert back["failed"] == 0 and back["duplicates"] == 2 and back["valid"] == 0


async def test_import_accepts_semicolons_excel_dates_and_ignores_unknown_columns(client, church_payload):
    h = await _church(client, church_payload, "excel")
    header = "First Name;Last Name;Gender;Status;Phone;Date of birth;Favourite colour"
    body = f"{header}\r\nAline;Uwase;Female;Visitor;{_phone()};17/05/1988;blue\r\n"
    report = (
        await client.post(
            "/api/v1/data/imports/members",
            files={"file": ("p.csv", body.encode("cp1252"), "text/csv")},
            data={"dry_run": "true"},
            headers=h,
        )
    ).json()
    assert report["valid"] == 1 and report["failed"] == 0
    assert any("favourite_colour" in n for n in report["notes"])


async def test_unusable_files_are_refused_plainly(client, church_payload):
    h = await _church(client, church_payload, "badfile")
    no_columns = await _import(client, h, _file("Aline,Uwase", header="first_name,last_name"))
    assert no_columns.status_code == 422 and "gender" in no_columns.json()["error"]["message"]
    empty = await _import(client, h, {"file": ("x.csv", b"", "text/csv")})
    assert empty.status_code == 422
    header_only = await _import(client, h, _file(header=HEADER))
    assert header_only.status_code == 422


async def test_one_church_never_sees_anothers_people(client, church_payload):
    mine = await _church(client, church_payload, "mine")
    other = await _church(client, church_payload, "other")
    assert (
        await _import(
            client, mine, _file(f"Aline,Uwase,female,visitor,{_phone()},,,,,"), dry_run=False, consent_confirmed=True
        )
    ).json()["committed"]
    theirs = (await client.get("/api/v1/data/exports/members.csv", headers=other)).content.decode("utf-8-sig")
    assert len(theirs.strip().splitlines()) == 1  # header only
