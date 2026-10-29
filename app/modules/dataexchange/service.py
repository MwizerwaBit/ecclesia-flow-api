"""Spreadsheet export and import.

Export: the header is always the dataset's columns, so an empty church still
gets a file in exactly the shape an import reads — that file is the template.

Import runs in two passes so a bad file never half-lands. Pass 1 reads and
checks every row and writes nothing. Pass 2 saves the good rows, and only runs
when the caller asked for a real import, confirmed consent, and either every row
was good or they chose to skip the bad ones. The request is one database
transaction, so anything unexpected rolls the whole import back.
"""

import csv
import io
import re
from datetime import date, datetime

from prisma import Prisma
from pydantic import ValidationError

from app.core import pii
from app.core.authz import Scope
from app.core.exceptions import AppError, NotFoundError
from app.core.spreadsheet import safe_cell, unsafe_cell
from app.modules.audit.service import write_audit
from app.modules.dataexchange import repository
from app.modules.dataexchange.datasets import DATASETS, Dataset
from app.modules.people import service as people_service
from app.modules.people.schemas import (
    AddressIn,
    ConsentIn,
    EmergencyContact,
    HouseholdChoice,
    MemberCreate,
)

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 1000
MAX_ISSUES = 200
MAX_CELL = 4000


class BadFileError(AppError):
    status_code = 422
    code = "bad_import_file"


def get_dataset(key: str) -> Dataset:
    dataset = DATASETS.get(key)
    if dataset is None:
        raise NotFoundError("No such dataset")
    return dataset


# ───────────────────────────── Export ─────────────────────────────


def _date_text(value) -> str:
    return "" if value is None else str(value)[:10]


def _yes_no(value) -> str:
    return "" if value is None else ("yes" if value else "no")


def _member_cells(r: dict) -> dict[str, str]:
    text = {k: ("" if r.get(k) is None else str(r[k])) for k in r}
    return {
        **text,
        "national_id": "",  # never stored in a form that can be read back out
        "address_city": text["city"],
        "address_state": text["state"],
        "address_country": text["country"],
        "address_postal_code": text["postal_code"],
        "date_of_birth": _date_text(r["date_of_birth"]),
        "joined_at": _date_text(r["joined_at"]),
        "baptism_date": _date_text(r["baptism_date"]),
        "is_baptised": _yes_no(r["is_baptised"]),
        "consent_communications": _yes_no(r["consent_communications"]),
        "groups": "; ".join(r["group_names"] or []),
    }


def _write_csv(header: list[str], rows: list[list[str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return "﻿" + out.getvalue()  # the BOM makes Excel read it as UTF-8


def template_csv(dataset_key: str) -> str:
    dataset = get_dataset(dataset_key)
    return _write_csv([c.key for c in dataset.columns], [])


async def export_csv(
    db: Prisma, scope: Scope, *, dataset_key: str, tenant_id: str, actor_user_id: str
) -> tuple[str, int]:
    """The whole dataset as CSV (header only when there is nothing yet) and the
    number of rows. Recorded in the audit log: this moves personal data out."""
    dataset = get_dataset(dataset_key)
    rows = scope.filter_members(await repository.export_members(db))
    keys = [c.key for c in dataset.columns]
    body = []
    for r in rows:
        cells = _member_cells(r)
        body.append([safe_cell(cells.get(k, "")) for k in keys])
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action="data.exported",
        resource_type=dataset.key,
        metadata={"rows": len(rows)},
    )
    return _write_csv(keys, body), len(rows)


# ───────────────────────────── Reading a file ─────────────────────────────


def _decode(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise BadFileError(f"That file is over {MAX_BYTES // (1024 * 1024)} MB. Split it into smaller files.")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")  # Excel's "CSV" on Windows


def _read_table(text: str) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        raise BadFileError("The file is empty. Download the template and fill it in.")
    header_line = lines[0]
    delimiter = max([",", ";", "\t"], key=header_line.count)  # Excel in some regions writes ;
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = [re.sub(r"\s+", "_", h.strip().lower()) for h in next(reader)]
        rows = []
        for number, cells in enumerate(reader, start=2):
            if not any(c.strip() for c in cells):
                continue
            if len(rows) >= MAX_ROWS:
                raise BadFileError(f"That file has more than {MAX_ROWS} rows. Split it and import it in parts.")
            record = {
                header[i]: unsafe_cell(c.strip())[:MAX_CELL] for i, c in enumerate(cells[: len(header)]) if header[i]
            }
            rows.append((number, record))
    except csv.Error as exc:
        raise BadFileError(f"That doesn't look like a CSV file ({exc}).") from exc
    return header, rows


_TRUE, _FALSE = {"yes", "y", "true", "1"}, {"no", "n", "false", "0"}


def _parse_bool(value: str, label: str, errors: list[str]) -> bool | None:
    v = value.lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    errors.append(f"{label}: write yes or no")
    return None


def _parse_date(value: str, label: str, errors: list[str]) -> date | None:
    for parse in (date.fromisoformat, lambda s: datetime.strptime(s, "%d/%m/%Y").date()):
        try:
            return parse(value)
        except ValueError:
            continue
    errors.append(f"{label}: use 2024-03-31 or 31/03/2024")
    return None


# ───────────────────────────── Members ─────────────────────────────

_DATE_FIELDS = ("date_of_birth", "joined_at", "baptism_date")
_ENUM_FIELDS = ("gender", "status", "id_type", "marital_status", "join_method", "household_role")
_PLAIN_FIELDS = (
    "first_name",
    "last_name",
    "phone",
    "email",
    "national_id",
    "title",
    "middle_name",
    "preferred_name",
    "whatsapp",
    "occupation",
    "employer",
    "envelope_number",
    "notes",
)
_FIELD_TO_COLUMN = {
    "address": "address_line1",
    "emergency_contact": "emergency_contact_name",
    "consent": "consent_communications",
}


def _readable(error: dict) -> str:
    message = error["msg"].removeprefix("Value error, ")
    loc = [str(p) for p in error["loc"] if not isinstance(p, int)]
    if not loc:
        return message
    column = _FIELD_TO_COLUMN.get(loc[0], loc[0])
    return f"{column}: {message}"


def _build_member(row: dict[str, str], known_groups: dict[str, str]) -> tuple[MemberCreate | None, list[str]]:
    """One spreadsheet row into the same object the registration form submits,
    so an imported person obeys exactly the rules a typed one does."""
    errors: list[str] = []
    data: dict = {k: row[k] for k in _PLAIN_FIELDS if row.get(k)}
    for k in _ENUM_FIELDS:
        if row.get(k):
            data[k] = row[k].lower().replace(" ", "_")
    for k in _DATE_FIELDS:
        if row.get(k):
            parsed = _parse_date(row[k], k, errors)
            if parsed:
                data[k] = parsed
    if row.get("is_baptised"):
        value = _parse_bool(row["is_baptised"], "is_baptised", errors)
        if value is not None:
            data["is_baptised"] = value
    communications = False
    if row.get("consent_communications"):
        communications = bool(_parse_bool(row["consent_communications"], "consent_communications", errors))

    address = {k: row.get(f"address_{k}", "") for k in ("line1", "city", "state", "country", "postal_code")}
    if any(address.values()):
        missing = [k for k in ("line1", "city", "country") if not address[k]]
        if missing:
            errors.append(
                "address_line1: give the address, city and country together (missing " + ", ".join(missing) + ")"
            )
        else:
            data["address"] = AddressIn(
                line1=address["line1"],
                city=address["city"],
                country=address["country"],
                state=address["state"] or None,
                postal_code=address["postal_code"] or None,
            )
    if row.get("emergency_contact_name") or row.get("emergency_contact_phone"):
        if row.get("emergency_contact_name") and row.get("emergency_contact_phone"):
            try:
                data["emergency_contact"] = EmergencyContact(
                    name=row["emergency_contact_name"],
                    phone=row["emergency_contact_phone"],
                    relationship=row.get("emergency_contact_relationship") or None,
                )
            except ValidationError:
                errors.append("emergency_contact_phone: enter a full phone number")
        else:
            errors.append("emergency_contact_name: give the contact's name and phone together")

    names = [g.strip() for g in re.split(r"[;|]", row.get("groups", "")) if g.strip()]
    group_ids: list[str] = []
    for name in names:
        group_id = known_groups.get(name.lower())
        if group_id is None:
            errors.append(f'groups: there is no group called "{name}". Create it first or fix the name')
        else:
            group_ids.append(group_id)

    data.setdefault("status", "active")
    if row.get("household_name"):
        data["household"] = HouseholdChoice(mode="new", name=row["household_name"])
    try:
        member = MemberCreate(
            **data,
            consent=ConsentIn(given_by="self", communications=communications),
            group_ids=group_ids,
        )
    except ValidationError as exc:
        errors.extend(_readable(e) for e in exc.errors())
        return None, errors
    return (None if errors else member), errors


def _phone_key(phone: str | None) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())[-9:]


async def _check_member_rows(db: Prisma, rows: list[tuple[int, dict[str, str]]]) -> list[dict]:
    """Pass 1. Reads every row and checks it against the file and the church's
    existing people. Writes nothing."""
    known_groups = await repository.group_ids_by_name(db)
    results: list[dict] = []
    seen_envelopes: dict[str, int] = {}
    seen_ids: dict[str, int] = {}
    seen_people: dict[tuple, int] = {}

    for number, row in rows:
        member, errors = _build_member(row, known_groups)
        name = f"{row.get('first_name', '')} {row.get('last_name', '')}".strip() or "(no name)"
        result = {"row": number, "status": "ok", "name": name, "messages": errors, "member": member}
        results.append(result)
        if member is None:
            result["status"] = "error"
            continue

        if member.envelope_number:
            if member.envelope_number in seen_envelopes:
                errors.append(f"envelope_number: also used on row {seen_envelopes[member.envelope_number]}")
            elif await repository.envelope_taken(db, member.envelope_number):
                errors.append(f"envelope_number: #{member.envelope_number} already belongs to someone else")
            seen_envelopes.setdefault(member.envelope_number, number)

        if member.national_id:
            id_type = member.id_type or "national_id"
            key = pii.normalise(id_type, member.national_id)
            existing = await repository.member_with_id_hash(db, pii.id_hash(id_type, member.national_id))
            if key in seen_ids:
                errors.append(f"national_id: also used on row {seen_ids[key]}")
            elif existing is not None:
                errors.append(f"national_id: already registered to {existing['first_name']} {existing['last_name']}")
            seen_ids.setdefault(key, number)

        if errors:
            result["status"] = "error"
            continue

        # The same person twice: same name and the same phone or email.
        person = (
            member.first_name.lower(),
            member.last_name.lower(),
            _phone_key(member.phone) or (member.email or "").lower(),
        )
        if person[2] and person in seen_people:
            result["status"] = "duplicate"
            errors.append(f"same person as row {seen_people[person]}; left out")
            continue
        seen_people.setdefault(person, number)

        for match in await people_service.find_duplicates(
            db,
            first_name=member.first_name,
            last_name=member.last_name,
            phone=member.phone,
            email=member.email,
            exclude_id=None,
        ):
            who = f"{match['member']['first_name']} {match['member']['last_name']}"
            reasons = set(match["reasons"])
            if "name" in reasons and reasons & {"phone", "email"}:
                result["status"] = "duplicate"
                errors.append(f"already on file as {who}; left out")
                break
            errors.append(f"possible match with {who} (same {' and '.join(sorted(reasons))}); imported anyway")
    return results


async def run_import(
    db: Prisma,
    scope: Scope,
    *,
    dataset_key: str,
    tenant_id: str,
    actor_user_id: str,
    data: bytes,
    dry_run: bool,
    consent_confirmed: bool,
    skip_invalid: bool,
) -> dict:
    dataset = get_dataset(dataset_key)
    if not dry_run and not consent_confirmed:
        raise BadFileError(
            "Confirm that these people agreed to have their details recorded before importing.", code="consent_required"
        )
    header, rows = _read_table(_decode(data))
    known = {c.key for c in dataset.columns}
    missing = [c.key for c in dataset.columns if c.required and c.key not in header]
    if missing:
        raise BadFileError(
            "The file is missing the column(s): "
            + ", ".join(missing)
            + ". Download the template for the right headers."
        )
    if not rows:
        raise BadFileError("The file has a header but no people in it.")

    notes = [f"Ignored the column '{h}': it isn't one we read." for h in header if h and h not in known]
    if scope.is_scoped and "groups" in header:
        notes.append("Groups outside your assigned branch are skipped.")

    results = await _check_member_rows(db, rows)
    failed = sum(r["status"] == "error" for r in results)
    importable = [r for r in results if r["status"] == "ok"]

    committed = False
    if not dry_run:
        if failed and not skip_invalid:
            notes.append("Nothing was imported: fix the rows below, or choose to skip them.")
        else:
            committed = True
            await _create_members(db, scope, tenant_id, importable)
            await write_audit(
                db,
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                action="data.imported",
                resource_type=dataset.key,
                metadata={
                    "rows": len(results),
                    "imported": sum(r["status"] == "ok" for r in results),
                    "skipped_duplicates": sum(r["status"] == "duplicate" for r in results),
                    "failed": sum(r["status"] == "error" for r in results),
                },
            )

    issues = [
        {"row": r["row"], "status": r["status"], "name": r["name"], "messages": r["messages"]}
        for r in results
        if r["status"] != "ok" or r["messages"]
    ]
    return {
        "dataset": dataset.key,
        "dry_run": not committed,
        "committed": committed,
        "total_rows": len(results),
        "valid": sum(r["status"] == "ok" for r in results),
        "duplicates": sum(r["status"] == "duplicate" for r in results),
        "failed": sum(r["status"] == "error" for r in results),
        "notes": notes,
        "issues": issues[:MAX_ISSUES],
        "issues_truncated": len(issues) > MAX_ISSUES,
    }


async def _create_members(db: Prisma, scope: Scope, tenant_id: str, rows: list[dict]) -> None:
    """Pass 2. People with the same household name share one household, and a
    household that already exists by that name is joined, not duplicated."""
    households = await repository.household_ids_by_name(db)
    for r in rows:
        member: MemberCreate = r["member"]
        member.unit_id = scope.assignable_unit(None)  # a scoped registrar imports into their own branch
        key = (
            member.household.name.strip().lower() if member.household.mode == "new" and member.household.name else None
        )
        if key and key in households:
            member.household = HouseholdChoice(mode="existing", household_id=households[key])
        try:
            created = await people_service.create_member(db, tenant_id, member, scope=scope)
        except AppError as exc:  # raised before any SQL, so the transaction is still healthy
            r["status"], r["messages"] = "error", [*r["messages"], exc.message]
            continue
        if key and created.get("household_id"):
            households.setdefault(key, str(created["household_id"]))
