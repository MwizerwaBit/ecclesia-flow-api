import re
from datetime import UTC, datetime

from prisma import Prisma

from app.core.exceptions import ConflictError, NotFoundError
from app.modules.groups import repository
from app.modules.groups.schemas import (
    AddMembers,
    GroupCreate,
    GroupRoleCreate,
    GroupRoleUpdate,
    GroupUpdate,
    MembershipUpdate,
)


def _schedule_in(schedule) -> dict:
    if schedule is None:
        return {}
    irregular = schedule.frequency == "irregular"
    return {
        "meeting_frequency": schedule.frequency,
        "meeting_day": None if irregular else schedule.day,
        "meeting_time": None if irregular else schedule.time,
        "meeting_location": schedule.location,
    }


def _group_out(row: dict, leaders: list[dict]) -> dict:
    return {
        **row,
        "schedule": {
            "frequency": row["meeting_frequency"],
            "day": row.get("meeting_day"),
            "time": row.get("meeting_time"),
            "location": row.get("meeting_location"),
        },
        "member_count": row.get("member_count") or 0,
        "leaders": [leader for leader in leaders if leader["group_id"] == row["id"]],
    }


def _roster_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "group_id": row["group_id"],
        "member_id": row["member_id"],
        "role": row["role"],
        "joined_at": row["joined_at"],
        "note": row.get("note"),
        "status": row.get("status") or "active",
        "invited_at": row.get("invited_at"),
        "responded_at": row.get("responded_at"),
        "role_name": row.get("role_name"),
        "member": {
            "id": row["m_id"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "preferred_name": row.get("preferred_name"),
            "photo_url": row.get("photo_url"),
            "initials": row["initials"],
            "status": row["status"],
            "unit_id": row.get("unit_id"),
            "unit_name": row.get("unit_name"),
            "envelope_number": row.get("envelope_number"),
            "last_seen_at": row.get("last_seen_at"),
            "email": row.get("email"),
            "phone": row.get("phone"),
            "whatsapp": row.get("whatsapp"),
            "gender": row.get("gender"),
            "joined_at": row.get("member_joined_at"),
            "household_id": row.get("household_id"),
            "associate_unit_ids": row.get("associate_unit_ids") or [],
        },
    }


async def list_groups(db: Prisma, *, include_archived: bool) -> list[dict]:
    rows = await repository.list_groups(db, include_archived=include_archived)
    leaders = await repository.leaders_for(db, [r["id"] for r in rows])
    return [_group_out(r, leaders) for r in rows]


async def get_group(db: Prisma, group_id: str) -> dict:
    row = await repository.get_group(db, group_id)
    if row is None:
        raise NotFoundError("No such group")
    leaders = await repository.leaders_for(db, [group_id])
    return {**_group_out(row, leaders), "roster": [_roster_out(r) for r in await repository.roster(db, group_id)]}


async def _check_unit(db: Prisma, unit_id: str | None) -> None:
    if unit_id and not await repository.unit_exists(db, unit_id):
        raise NotFoundError("No such unit")


async def create_group(db: Prisma, tenant_id: str, payload: GroupCreate) -> dict:
    if await repository.name_taken(db, payload.name):
        raise ConflictError(f'There is already a group called "{payload.name}"')
    await _check_unit(db, payload.unit_id)
    data = payload.model_dump(exclude={"schedule"}, exclude_none=True)
    data.update(_schedule_in(payload.schedule))
    group = await repository.create_group(db, {**data, "tenant_id": tenant_id})
    return await get_group(db, group.id)


async def update_group(db: Prisma, group_id: str, payload: GroupUpdate) -> dict:
    if await repository.get_group(db, group_id) is None:
        raise NotFoundError("No such group")
    if payload.name and await repository.name_taken(db, payload.name, exclude_id=group_id):
        raise ConflictError(f'There is already a group called "{payload.name}"')
    await _check_unit(db, payload.unit_id)
    data = payload.model_dump(exclude={"schedule"}, exclude_unset=True)
    if "schedule" in payload.model_fields_set:
        data.update(_schedule_in(payload.schedule))
    await repository.update_group(db, group_id, data)
    return await get_group(db, group_id)


async def add_members(
    db: Prisma, tenant_id: str, group_id: str, payload: AddMembers, *, invited_by_user_id: str
) -> list[dict]:
    """Adds people to a group. Someone with their own login is *invited* and
    joins once they accept in their portal; someone without one (a child, an
    elderly member staff look after) joins directly. Anyone already on the
    roster keeps their place; ids outside this church are ignored."""
    if await repository.get_group(db, group_id) is None:
        raise NotFoundError("No such group")
    if await repository.get_role(db, payload.role) is None:
        raise NotFoundError("No such group role")
    already = await repository.membership_member_ids(db, group_id)
    now = datetime.now(UTC)
    added = []
    for member in await repository.existing_members(db, list(dict.fromkeys(payload.member_ids))):
        if member["id"] in already:
            continue
        invited = member["user_id"] is not None
        membership = await repository.add_membership(
            db,
            {
                "tenant_id": tenant_id,
                "group_id": group_id,
                "member_id": member["id"],
                "role": payload.role,
                "status": "invited" if invited else "active",
                "invited_by_user_id": invited_by_user_id,
                "invited_at": now if invited else None,
            },
        )
        added.append(membership.model_dump())
    return added


async def update_membership(db: Prisma, group_id: str, member_id: str, payload: MembershipUpdate) -> dict:
    membership = await repository.find_membership(db, group_id, member_id)
    if membership is None:
        raise NotFoundError("That person is not in this group")
    if payload.role is not None and await repository.get_role(db, payload.role) is None:
        raise NotFoundError("No such group role")
    updated = await repository.update_membership(db, membership.id, payload.model_dump(exclude_unset=True))
    return updated.model_dump()


async def remove_member(db: Prisma, group_id: str, member_id: str) -> None:
    membership = await repository.find_membership(db, group_id, member_id)
    if membership is None:
        raise NotFoundError("That person is not in this group")
    await repository.delete_membership(db, membership.id)


# ───────────────────────────── Group roles ─────────────────────────────


def _role_out(role) -> dict:
    return {
        "id": role.id,
        "key": role.key,
        "name": role.name,
        "capabilities": role.capabilities,
        "rank": role.rank,
        "is_system": role.is_system,
    }


def _key_from(name: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40]
    return key if re.match(r"^[a-z][a-z0-9_]{1,39}$", key) else f"role_{key}"[:40]


async def list_roles(db: Prisma) -> list[dict]:
    return [_role_out(r) for r in await repository.list_roles(db)]


async def create_role(db: Prisma, tenant_id: str, payload: GroupRoleCreate) -> dict:
    key = _key_from(payload.name)
    if await repository.get_role(db, key) is not None:
        raise ConflictError(f'A group role called "{payload.name}" already exists')
    role = await db.grouprole.create(
        data={
            "tenant_id": tenant_id,
            "key": key,
            "name": payload.name,
            "capabilities": sorted(set(payload.capabilities)),
            "rank": payload.rank,
        }
    )
    return _role_out(role)


async def update_role(db: Prisma, role_id: str, payload: GroupRoleUpdate) -> dict:
    role = await repository.get_role_by_id(db, role_id)
    if role is None:
        raise NotFoundError("No such group role")
    data = payload.model_dump(exclude_unset=True)
    if role.is_system:
        # Built-in roles can be renamed, but their powers and order are fixed
        # so every church keeps one role that can always run a group.
        data = {k: v for k, v in data.items() if k == "name"}
    if "capabilities" in data:
        data["capabilities"] = sorted(set(data["capabilities"]))
    return _role_out(await db.grouprole.update(where={"id": role_id}, data=data))


async def delete_role(db: Prisma, role_id: str) -> None:
    role = await repository.get_role_by_id(db, role_id)
    if role is None:
        raise NotFoundError("No such group role")
    if role.is_system:
        raise ConflictError("Built-in group roles can't be deleted")
    if await repository.role_in_use(db, role.key):
        raise ConflictError("Move everyone off this role before deleting it")
    await db.grouprole.delete(where={"id": role_id})
