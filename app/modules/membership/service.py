"""Unit membership — where a person belongs inside one church, over time.

Every person has at most one current *home* unit (mirrored in
``members.unit_id``, which the rest of the app filters on) and any number of
*associate* units: a choir member who belongs to the cathedral parish but
serves at an outstation, a student attending a second campus. Nothing is
overwritten: moving someone closes their old home membership as
``transferred`` and opens a new one, so the history stays readable.
"""

from prisma import Prisma

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.modules.audit.service import write_audit
from app.modules.membership import repository
from app.modules.membership.schemas import AssociateMembershipCreate, MoveHomeUnitRequest

# Allowed lifecycle moves, by current status.
_TRANSITIONS = {
    "active": {"suspended", "ended"},
    "suspended": {"active", "ended"},
    "transferred": set(),
    "ended": set(),
}


async def list_for_member(db: Prisma, member_id: str) -> list[dict]:
    return await repository.list_for_member(db, member_id)


async def _unit_exists(db: Prisma, unit_id: str) -> None:
    if await db.hierarchyunit.find_unique(where={"id": unit_id}) is None:
        raise NotFoundError("No such unit")


async def add_associate(
    db: Prisma, *, tenant_id: str, user_id: str, member_id: str, payload: AssociateMembershipCreate
) -> dict:
    await _unit_exists(db, payload.unit_id)
    member = await db.member.find_unique(where={"id": member_id})
    if member is None:
        raise NotFoundError("No such member")
    if member.unit_id == payload.unit_id:
        raise ConflictError("That is already their home unit", code="already_member")
    if await repository.current_in_unit(db, member_id, payload.unit_id):
        raise ConflictError("They already belong to that unit", code="already_member")
    membership_id = await repository.create(
        db,
        tenant_id=tenant_id,
        member_id=member_id,
        unit_id=payload.unit_id,
        kind="associate",
        started_on=payload.started_on,
        user_id=user_id,
    )
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        action="membership.associate_added",
        resource_type="unit_membership",
        resource_id=membership_id,
        metadata={"member_id": member_id, "unit_id": payload.unit_id},
    )
    return await repository.get(db, membership_id)


async def move_home(
    db: Prisma, *, tenant_id: str, user_id: str, member_id: str, payload: MoveHomeUnitRequest
) -> list[dict]:
    """A transfer inside the church: the old home membership is closed as
    transferred (with the reason), a new one starts today, and an associate
    membership in the new unit, if any, is closed because it becomes home."""
    await _unit_exists(db, payload.unit_id)
    member = await db.member.find_unique(where={"id": member_id})
    if member is None:
        raise NotFoundError("No such member")
    if member.unit_id == payload.unit_id:
        raise ConflictError("That is already their home unit", code="already_member")

    old_home = await repository.current_home(db, member_id)
    if old_home is not None:
        await repository.set_status(db, old_home["id"], "transferred", payload.reason)
    associate = await repository.current_in_unit(db, member_id, payload.unit_id)
    if associate is not None:
        await repository.set_status(db, associate["id"], "ended", "Became their home unit")
    await repository.create(
        db,
        tenant_id=tenant_id,
        member_id=member_id,
        unit_id=payload.unit_id,
        kind="home",
        started_on=None,
        user_id=user_id,
    )
    # The members_sync_home_unit trigger sees the new home row already exists
    # and leaves the history alone.
    await repository.set_member_home_unit(db, member_id, payload.unit_id)
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        action="membership.home_moved",
        resource_type="member",
        resource_id=member_id,
        metadata={"from_unit_id": member.unit_id, "to_unit_id": payload.unit_id, "reason": payload.reason},
    )
    return await repository.list_for_member(db, member_id)


async def change_status(
    db: Prisma, *, tenant_id: str, user_id: str, membership: dict, status: str, reason: str | None
) -> dict:
    current = membership["status"]
    if status not in _TRANSITIONS[current]:
        raise ConflictError(f"A {current} membership can't become {status}", code="invalid_transition")
    if membership["kind"] == "home" and status == "ended":
        raise AppError(
            "A home membership ends by moving the person to another unit", code="home_membership_ends_by_move"
        )
    await repository.set_status(db, membership["id"], status, reason)
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        action=f"membership.{status}",
        resource_type="unit_membership",
        resource_id=membership["id"],
        metadata={"member_id": membership["member_id"], "unit_id": membership["unit_id"], "reason": reason},
    )
    return await repository.get(db, membership["id"])


async def get(db: Prisma, membership_id: str) -> dict:
    row = await repository.get(db, membership_id)
    if row is None:
        raise NotFoundError("No such membership")
    return row
