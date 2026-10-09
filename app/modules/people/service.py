from datetime import UTC, datetime

from prisma import Prisma

from app.core import pii
from app.core.exceptions import ConflictError, NotFoundError
from app.modules.people import repository
from app.modules.people.schemas import (
    AddressIn,
    HouseholdChoice,
    MemberCreate,
    MemberUpdate,
    PastoralNoteCreate,
    SacramentalRecordCreate,
    VisitorQuickAdd,
)


def _address_out(row: dict) -> dict | None:
    if not row.get("address_line1"):
        return None
    return {
        "line1": row["address_line1"],
        "line2": row.get("address_line2"),
        "city": row.get("city"),
        "state": row.get("state"),
        "country": row.get("country"),
        "postal_code": row.get("postal_code"),
    }


def _address_in(address: AddressIn | None) -> dict:
    if address is None:
        return {}
    return {
        "address_line1": address.line1,
        "address_line2": address.line2,
        "city": address.city,
        "state": address.state,
        "country": address.country,
        "postal_code": address.postal_code,
    }


def _detail_out(row: dict, groups: list[dict] | None = None) -> dict:
    attended = row.get("attended_count") or 0
    total_events = row.get("total_individual_events") or 0
    attendance_rate = (attended / total_events) if total_events else None
    emergency = (
        {
            "name": row["emergency_contact_name"],
            "phone": row.get("emergency_contact_phone") or "",
            "relationship": row.get("emergency_contact_relationship"),
        }
        if row.get("emergency_contact_name")
        else None
    )
    consent = (
        {
            "data_processing_at": row["consent_data_processing_at"],
            "given_by": row.get("consent_given_by") or "self",
            "communications": bool(row.get("consent_communications")),
            "directory_visible": bool(row.get("directory_visible", True)),
        }
        if row.get("consent_data_processing_at")
        else None
    )
    return {
        **row,
        "address": _address_out(row),
        "attendance_rate": attendance_rate,
        "total_giving": float(row.get("total_giving") or 0),
        "giving_this_year": float(row.get("giving_this_year") or 0),
        "emergency_contact": emergency,
        "consent": consent,
        "groups": groups or [],
    }


def _emergency_in(contact) -> dict:
    if contact is None:
        return dict.fromkeys(("emergency_contact_name", "emergency_contact_phone", "emergency_contact_relationship"))
    return {
        "emergency_contact_name": contact.name,
        "emergency_contact_phone": contact.phone,
        "emergency_contact_relationship": contact.relationship,
    }


def _consent_in(consent) -> dict:
    return {
        "consent_data_processing_at": datetime.now(UTC),
        "consent_given_by": consent.given_by,
        "consent_communications": consent.communications,
        "directory_visible": consent.directory_visible,
    }


async def _protected_id(db: Prisma, id_type: str, number: str, exclude_member_id: str | None = None) -> dict:
    """The stored form of an ID number, after checking nobody else in this
    church already has it (the unique index is the backstop)."""
    protected = pii.protect(id_type, number)
    existing = await repository.find_by_id_hash(db, protected["national_id_hash"])
    if existing is not None and existing["id"] != exclude_member_id:
        raise ConflictError(
            f"Someone with this ID number is already registered ({existing['first_name']} {existing['last_name']}).",
            code="duplicate_id",
        )
    return protected


async def _with_groups(db: Prisma, rows: list[dict]) -> list[dict]:
    refs = await repository.group_refs_for(db, [r["id"] for r in rows])
    return [{**r, "groups": refs.get(r["id"], [])} for r in rows]


_KEEP = object()


async def _resolve_household(db: Prisma, tenant_id: str, choice: HouseholdChoice | None, address: AddressIn | None):
    """The household id to set, None to clear it, or _KEEP to leave it as is."""
    if choice is None:
        return _KEEP
    if choice.mode == "none":
        return None
    if choice.mode == "existing":
        if not await repository.household_exists(db, choice.household_id):
            raise NotFoundError("No such household")
        return choice.household_id
    household = await repository.create_household(db, tenant_id, {"name": choice.name.strip(), **_address_in(address)})
    return household.id


async def list_members(db: Prisma, *, search: str | None, status: str | None, unit_id: str | None) -> list[dict]:
    return await _with_groups(db, await repository.list_members(db, search=search, status=status, unit_id=unit_id))


async def get_not_seen_recently(db: Prisma) -> list[dict]:
    return await _with_groups(db, await repository.get_not_seen_recently(db))


async def get_member_detail(db: Prisma, member_id: str) -> dict:
    row = await repository.get_detail_row(db, member_id)
    if row is None:
        raise NotFoundError("No such member")
    refs = await repository.group_refs_for(db, [member_id])
    return _detail_out(row, refs.get(member_id))


async def get_member_by_user_id(db: Prisma, user_id: str) -> dict | None:
    row = await repository.get_detail_row_by_user_id(db, user_id)
    if not row:
        return None
    refs = await repository.group_refs_for(db, [row["id"]])
    return _detail_out(row, refs.get(row["id"]))


def _digits(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


async def find_duplicates(
    db: Prisma,
    *,
    first_name: str | None,
    last_name: str | None,
    phone: str | None,
    email: str | None,
    exclude_id: str | None,
) -> list[dict]:
    first = (first_name or "").strip().lower() or None
    last = (last_name or "").strip().lower() or None
    digits = _digits(phone) or None
    mail = (email or "").strip().lower() or None
    rows = await repository.find_duplicates(
        db, first_name=first, last_name=last, phone_digits=digits, email=mail, exclude_id=exclude_id
    )
    out = []
    for r in await _with_groups(db, rows):
        reasons = []
        if first and last and r["first_name"].lower() == first and r["last_name"].lower() == last:
            reasons.append("name")
        if digits and len(digits) >= 7 and _digits(r.get("phone"))[-9:] == digits[-9:]:
            reasons.append("phone")
        if mail and (r.get("email") or "").lower() == mail:
            reasons.append("email")
        out.append({"member": r, "reasons": reasons})
    return out


async def next_envelope_number(db: Prisma) -> str:
    return await repository.next_envelope_number(db)


async def list_households(db: Prisma) -> list[dict]:
    out = []
    for row in await repository.list_households(db):
        members = await repository.list_household_members(db, row["id"])
        out.append({**row, "address": _address_out(row), "total_giving": 0.0, "members": members})
    return out


async def create_member(db: Prisma, tenant_id: str, payload: MemberCreate, *, scope=None) -> dict:
    """Registers one person into the caller's church — the member row, their
    household and their group memberships in the request's single transaction,
    so a registration lands whole or not at all."""
    if payload.envelope_number and await repository.envelope_taken(db, payload.envelope_number):
        raise ConflictError(f"Envelope #{payload.envelope_number} already belongs to someone else")

    household_id = await _resolve_household(db, tenant_id, payload.household, payload.address)
    group_ids = await repository.existing_group_ids(db, list(dict.fromkeys(payload.group_ids)))
    if scope is not None and scope.is_scoped:
        # Joining a group is a write to that group: only groups inside scope.
        units = await repository.group_units(db, group_ids)
        group_ids = [g for g in group_ids if scope.unit_writable(units.get(g))]

    data = payload.model_dump(
        exclude={"address", "household", "group_ids", "emergency_contact", "consent", "national_id", "id_type"},
        exclude_none=True,
    )
    if payload.national_id:
        data.update(await _protected_id(db, payload.id_type or "national_id", payload.national_id))
    data.update(_address_in(payload.address))
    data.update({k: v for k, v in _emergency_in(payload.emergency_contact).items() if v is not None})
    data.update(_consent_in(payload.consent))
    if payload.join_method != "transfer":
        data.pop("previous_church", None)
    if not payload.is_baptised:
        data.pop("baptism_date", None)
    if household_id:
        data["household_id"] = household_id
    else:
        data.pop("household_role", None)

    member = await repository.create_member(db, tenant_id, data)
    if household_id and payload.household_role == "head":
        await repository.set_household_head(db, household_id, member.id)
    await repository.add_group_memberships(db, tenant_id, member.id, group_ids)
    return await get_member_detail(db, member.id)


async def update_member(db: Prisma, member_id: str, payload: MemberUpdate) -> dict:
    existing = await repository.get_member(db, member_id)
    if existing is None:
        raise NotFoundError("No such member")
    if payload.envelope_number and await repository.envelope_taken(db, payload.envelope_number, exclude_id=member_id):
        raise ConflictError(f"Envelope #{payload.envelope_number} already belongs to someone else")

    data = payload.model_dump(
        exclude={"address", "household", "emergency_contact", "consent", "national_id", "id_type"}, exclude_unset=True
    )
    if payload.national_id:
        data.update(
            await _protected_id(
                db, payload.id_type or existing.id_type or "national_id", payload.national_id, member_id
            )
        )
    if "address" in payload.model_fields_set:
        data.update(_address_in(payload.address))
    if "emergency_contact" in payload.model_fields_set:
        data.update(_emergency_in(payload.emergency_contact))
    # Consent is recorded once; an update can add it to an older record but never rewrite it.
    if payload.consent is not None and existing.consent_data_processing_at is None:
        data.update(_consent_in(payload.consent))

    household_id = await _resolve_household(db, existing.tenant_id, payload.household, payload.address)
    if household_id is not _KEEP:
        data["household_id"] = household_id

    await repository.update_member(db, member_id, data)
    target_household = data.get("household_id", existing.household_id)
    if target_household and (payload.household_role or existing.household_role) == "head":
        await repository.set_household_head(db, target_household, member_id)
    return await get_member_detail(db, member_id)


async def quick_add_visitor(
    db: Prisma, tenant_id: str, payload: VisitorQuickAdd, *, unit_id: str | None = None
) -> dict:
    member = await repository.create_member(
        db,
        tenant_id,
        {
            "first_name": payload.first_name,
            "last_name": payload.last_name,
            "phone": payload.phone,
            "status": "visitor",
            **({"unit_id": unit_id} if unit_id else {}),
        },
    )
    return {
        "id": member.id,
        "first_name": member.first_name,
        "last_name": member.last_name,
        "preferred_name": member.preferred_name,
        "photo_url": member.photo_url,
        "initials": (member.first_name[:1] + member.last_name[:1]).upper(),
        "status": member.status,
        "unit_name": None,
        "unit_id": member.unit_id,
        "envelope_number": member.envelope_number,
        "last_seen_at": member.last_seen_at,
        "phone": member.phone,
        "groups": [],
    }


async def get_household(db: Prisma, household_id: str) -> dict | None:
    row = await repository.get_household(db, household_id)
    if row is None:
        return None
    members = await repository.list_household_members(db, household_id)
    return {
        **row,
        "address": _address_out(row),
        "total_giving": float(row.get("total_giving") or 0),
        "members": members,
    }


async def list_pastoral_notes(db: Prisma, member_id: str) -> list[dict]:
    return await repository.list_pastoral_notes(db, member_id)


async def create_pastoral_note(
    db: Prisma, *, tenant_id: str, member_id: str, author_user_id: str, payload: PastoralNoteCreate
):
    return await repository.create_pastoral_note(
        db,
        tenant_id=tenant_id,
        member_id=member_id,
        author_user_id=author_user_id,
        content=payload.content,
        is_private=payload.is_private,
    )


async def list_sacramental_records(db: Prisma, member_id: str):
    return await repository.list_sacramental_records(db, member_id)


async def create_sacramental_record(db: Prisma, *, tenant_id: str, member_id: str, payload: SacramentalRecordCreate):
    return await repository.create_sacramental_record(
        db, tenant_id=tenant_id, member_id=member_id, data=payload.model_dump()
    )


async def list_visitor_followups(db: Prisma) -> list[dict]:
    rows = await repository.list_visitor_followups(db)
    out = []
    for r in rows:
        out.append(
            {
                "id": r["member_id"],
                "member_id": r["member_id"],
                "member": {
                    "id": r["member_id"],
                    "first_name": r["first_name"],
                    "last_name": r["last_name"],
                    "preferred_name": r["preferred_name"],
                    "photo_url": r["photo_url"],
                    "initials": r["initials"],
                    "status": r["status"],
                    "unit_name": r["unit_name"],
                    "envelope_number": r["envelope_number"],
                    "last_seen_at": r["last_seen_at"],
                },
                "days_since_visit": r["days_since_visit"] or 0,
                "visit_date": r["visit_date"],
                "assigned_to_id": None,
                "assigned_to_name": None,
                "status": "pending",
                "notes": None,
            }
        )
    return out
