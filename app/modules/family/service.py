"""Family relationships and the access they grant.

Two different questions live here and are kept apart on purpose:

  RECORDING  who is whose parent, guardian or spouse. Staff do this, subject
             to the usual people permissions and unit scope.
  ACTING     what a signed-in parent or guardian may then do for a child. This
             is its own rule, not a side effect of the record:

               - only a parent or guardian relationship grants anything;
                 a spouse relationship grants nothing at all;
               - only while the other person is UNDER 18. It ends on their
                 birthday without anyone having to close the relationship,
                 and an unknown date of birth grants nothing (fail closed);
               - what they get is a narrow view: contact and emergency
                 details and group names. No giving, notes or ID details.
"""

from datetime import date, datetime

from prisma import Prisma

from app.core.exceptions import ConflictError, NotFoundError
from app.modules.audit.service import write_audit
from app.modules.family import repository
from app.modules.family.schemas import RelationshipCreate
from app.modules.people import service as people_service

ADULT_AGE = 18


def _as_date(value: object) -> date | None:
    """Raw SQL hands dates back as str, datetime or date depending on the driver path."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _age(raw_dob: object) -> int | None:
    dob = _as_date(raw_dob)
    if dob is None:
        return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _is_minor(dob: object) -> bool:
    age = _age(dob)
    return age is not None and age < ADULT_AGE


def grants_access(role: str, other_dob: object) -> bool:
    """Does this relationship, seen from the parent's side, let them act for
    the other person today? `role` is who the OTHER person is to me."""
    return role in ("child", "ward") and _is_minor(other_dob)


def _read(row: dict) -> dict:
    dob = row["other_dob"]
    return {
        "id": str(row["id"]),
        "role": row["role"],
        "other_member_id": str(row["other_member_id"]),
        "other_name": row["other_name"],
        "other_photo_url": row["other_photo_url"],
        "other_age": _age(dob),
        "started_on": row["started_on"],
        "ended_on": row["ended_on"],
        "grants_access": row["ended_on"] is None and grants_access(row["role"], dob),
    }


async def list_for_member(db: Prisma, member_id: str) -> list[dict]:
    rows = await repository.list_for_member(db, member_id, live_only=False)
    return [_read(r) for r in rows]


async def add(db: Prisma, *, tenant_id: str, user_id: str, member_id: str, payload: RelationshipCreate) -> list[dict]:
    related_id_str = str(payload.related_member_id)
    if related_id_str == member_id:
        raise ConflictError("A person can't be their own relative")
    me = await repository.get_member_brief(db, member_id)
    other = await repository.get_member_brief(db, related_id_str)
    if me is None or other is None:
        raise NotFoundError("No such person")

    if payload.role == "spouse":
        if _is_minor(me["date_of_birth"]) or _is_minor(other["date_of_birth"]):
            raise ConflictError("Spouses must both be adults")
        for person in (me, other):
            if await repository.has_live_spouse(db, str(person["id"])):
                raise ConflictError("One of them already has a spouse on record. End that first.")
        subject_id, related_id = sorted([str(me["id"]), str(other["id"])])
        kind = "spouse"
    else:
        # Work out who the adult is and who the dependant is.
        if payload.role in ("parent", "guardian"):
            parent, child, kind = other, me, payload.role
        else:
            parent, child, kind = me, other, "parent" if payload.role == "child" else "guardian"
        if _is_minor(parent["date_of_birth"]):
            raise ConflictError("A parent or guardian must be an adult")
        parent_dob, child_dob = _as_date(parent["date_of_birth"]), _as_date(child["date_of_birth"])
        if parent_dob is not None and child_dob is not None and parent_dob >= child_dob:
            raise ConflictError("A parent can't be younger than their child")
        # The reverse can't also hold: nobody is their own descendant.
        for reverse_kind in ("parent", "guardian"):
            if await repository.live_exists(db, str(parent["id"]), str(child["id"]), reverse_kind):
                raise ConflictError("That would make each of them the other's parent or guardian")
        subject_id, related_id = str(child["id"]), str(parent["id"])

    if await repository.live_exists(db, subject_id, related_id, kind):
        raise ConflictError("That relationship is already recorded")

    relationship_id = await repository.create(
        db, tenant_id=tenant_id, member_id=subject_id, related_member_id=related_id, kind=kind, user_id=user_id
    )
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        action="family.relationship_added",
        resource_type="member_relationship",
        resource_id=relationship_id,
        # Ids only; names stay out of the audit log.
        metadata={"member_id": subject_id, "related_member_id": related_id, "kind": kind},
    )
    return await list_for_member(db, member_id)


async def end(db: Prisma, *, tenant_id: str, user_id: str, member_id: str, relationship_id: str) -> None:
    row = await repository.get(db, relationship_id)
    if row is None or member_id not in (str(row["member_id"]), str(row["related_member_id"])):
        raise NotFoundError("No such relationship")
    if row["ended_on"] is not None:
        raise ConflictError("That relationship has already ended")
    await repository.end(db, relationship_id, user_id)
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        action="family.relationship_ended",
        resource_type="member_relationship",
        resource_id=relationship_id,
        metadata={"kind": row["kind"]},
    )


# ── The signed-in parent or guardian ────────────────────────────────────


async def _me(db: Prisma, user_id: str) -> dict:
    member = await people_service.get_member_by_user_id(db, user_id)
    if member is None:
        raise NotFoundError("Your login isn't linked to a member record in this church yet")
    return member


async def my_family(db: Prisma, user_id: str) -> dict:
    me = await _me(db, user_id)
    rows = await repository.list_for_member(db, me["id"], live_only=True)
    relationships = [_read(r) for r in rows]
    return {"relationships": relationships, "dependants": [r for r in relationships if r["grants_access"]]}


async def dependant_view(db: Prisma, user_id: str, child_id: str) -> dict:
    overview = await my_family(db, user_id)
    # Same answer for "not yours" and "doesn't exist": nothing to learn.
    if not any(d["other_member_id"] == child_id for d in overview["dependants"]):
        raise NotFoundError("No such person")
    child = await people_service.get_member_detail(db, child_id)
    emergency = child.get("emergency_contact") or {}
    return {
        "id": child["id"],
        "first_name": child["first_name"],
        "last_name": child["last_name"],
        "preferred_name": child.get("preferred_name"),
        "photo_url": child.get("photo_url"),
        "date_of_birth": child.get("date_of_birth"),
        "age": _age(child.get("date_of_birth")),
        "email": child.get("email"),
        "phone": child.get("phone"),
        "emergency_contact_name": emergency.get("name"),
        "emergency_contact_phone": emergency.get("phone"),
        "groups": await repository.member_group_names(db, child_id),
    }
