"""The member's own view: their record, their groups, their events.

Authorisation here is not unit scope but *relationship*: everything is
resolved from the member record linked to the signed-in user, and group
powers come from that member's role inside each group (church-defined group
roles with capabilities). A member sees only groups they belong to or are
invited to; a group leader manages only their own group, and only people
ranked below them.
"""

from datetime import UTC, datetime

from prisma import Prisma

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.activity import service as events_service
from app.modules.groups import repository as groups_repository
from app.modules.people import service as people_service


async def own_member(db: Prisma, user_id: str) -> dict:
    member = await people_service.get_member_by_user_id(db, user_id)
    if member is None:
        raise NotFoundError("Your login isn't linked to a member record in this church yet")
    return member


async def my_groups(db: Prisma, user_id: str) -> list[dict]:
    member = await own_member(db, user_id)
    return await db.query_raw(
        """
        select gm.id as membership_id, gm.status, gm.role, gr.name as role_name, gr.capabilities, gr.rank,
               gm.invited_at, gm.responded_at,
               g.id as group_id, g.name, g.type, g.description, g.color, g.meeting_frequency, g.meeting_day,
               g.meeting_time, g.meeting_location,
               (select count(*) from group_memberships x where x.group_id = g.id and x.status = 'active')::int
                 as member_count
        from group_memberships gm
        join groups g on g.id = gm.group_id and not g.is_archived
        join group_roles gr on gr.tenant_id = gm.tenant_id and gr.key = gm.role
        where gm.member_id = $1::uuid and gm.status in ('invited', 'active')
        order by gm.status desc, g.name
        """,
        member["id"],
    )


async def respond(db: Prisma, user_id: str, membership_id: str, accept: bool) -> list[dict]:
    member = await own_member(db, user_id)
    membership = await db.groupmembership.find_unique(where={"id": membership_id})
    if membership is None or membership.member_id != member["id"]:
        raise NotFoundError("No such invitation")
    if membership.status != "invited":
        raise ConflictError("You've already answered this invitation")
    await db.groupmembership.update(
        where={"id": membership_id},
        data={"status": "active" if accept else "declined", "responded_at": datetime.now(UTC)},
    )
    return await my_groups(db, user_id)


async def _my_role(db: Prisma, user_id: str, group_id: str) -> tuple[dict, object]:
    """My member record and my ACTIVE membership's role in this group, or 404."""
    member = await own_member(db, user_id)
    membership = await db.groupmembership.find_first(
        where={"group_id": group_id, "member_id": member["id"], "status": "active"}
    )
    if membership is None:
        raise NotFoundError("No such group")
    role = await groups_repository.get_role(db, membership.role)
    return member, role


def _require(role, capability: str) -> None:
    if capability not in (role.capabilities or []):
        raise ForbiddenError("Your role in this group doesn't allow that", code="group_capability")


def _outranks(mine, theirs) -> bool:
    """Lower rank = more senior. Leaders (rank 0) may manage fellow leaders;
    everyone else only people strictly below them."""
    return mine.rank == 0 or mine.rank < theirs.rank


async def group_detail(db: Prisma, user_id: str, group_id: str) -> dict:
    member, role = await _my_role(db, user_id, group_id)
    group = await db.group.find_unique(where={"id": group_id})
    can_manage = "manage_roster" in (role.capabilities or [])
    rows = await groups_repository.roster(db, group_id)
    roster = []
    for r in rows:
        if r["status"] == "declined" or (r["status"] == "invited" and not can_manage):
            continue
        entry = {
            "member_id": r["member_id"],
            "first_name": r["first_name"],
            "last_name": r["last_name"],
            "photo_url": r.get("photo_url"),
            "role": r["role"],
            "role_name": r.get("role_name"),
            "status": r["status"],
            "note": r.get("note") if can_manage else None,
        }
        # Contact details only for those who run the group.
        if can_manage:
            entry.update(phone=r.get("phone"), email=r.get("email"))
        roster.append(entry)
    return {
        "group": {
            "id": group.id,
            "name": group.name,
            "type": group.type,
            "description": group.description,
            "color": group.color,
            "meeting_frequency": group.meeting_frequency,
            "meeting_day": group.meeting_day,
            "meeting_time": group.meeting_time,
            "meeting_location": group.meeting_location,
        },
        "my_role": {"key": role.key, "name": role.name, "capabilities": role.capabilities, "rank": role.rank},
        "roster": roster,
        "assignable_roles": [
            {"key": r.key, "name": r.name}
            for r in await groups_repository.list_roles(db)
            if can_manage and (role.rank == 0 or r.rank > role.rank)
        ],
    }


async def update_group(db: Prisma, user_id: str, group_id: str, data: dict) -> dict:
    _, role = await _my_role(db, user_id, group_id)
    _require(role, "edit_group")
    await db.group.update(where={"id": group_id}, data=data)
    return await group_detail(db, user_id, group_id)


async def candidates(db: Prisma, user_id: str, group_id: str, search: str) -> list[dict]:
    """People a group leader can invite. Requires a real search term and
    returns names only — enough to pick someone, never a directory dump."""
    _, role = await _my_role(db, user_id, group_id)
    _require(role, "manage_roster")
    term = search.strip().lower()
    if len(term) < 2:
        return []
    return await db.query_raw(
        """
        select m.id, m.first_name, m.last_name, m.photo_url
        from members m
        where m.status in ('active', 'visitor', 'prospect')
          and lower(m.first_name || ' ' || m.last_name) like $2
          and not exists (select 1 from group_memberships gm where gm.group_id = $1::uuid and gm.member_id = m.id
                          and gm.status in ('invited', 'active'))
        order by m.last_name, m.first_name
        limit 20
        """,
        group_id,
        f"%{term}%",
    )


async def invite(db: Prisma, tenant_id: str, user_id: str, group_id: str, member_ids: list[str], role_key: str) -> dict:
    _, role = await _my_role(db, user_id, group_id)
    _require(role, "manage_roster")
    target = await groups_repository.get_role(db, role_key)
    if target is None:
        raise NotFoundError("No such group role")
    if not _outranks(role, target):
        raise ForbiddenError("You can only give roles below your own", code="group_rank")
    now = datetime.now(UTC)
    existing = {m.member_id: m for m in await db.groupmembership.find_many(where={"group_id": group_id})}
    for member_id in dict.fromkeys(member_ids):
        if await db.member.find_unique(where={"id": member_id}) is None:
            continue
        current = existing.get(member_id)
        if current is not None and current.status in ("active", "invited"):
            continue
        data = {
            "role": role_key,
            "status": "invited",
            "invited_by_user_id": user_id,
            "invited_at": now,
            "responded_at": None,
        }
        if current is not None:  # previously declined: invite again
            await db.groupmembership.update(where={"id": current.id}, data=data)
        else:
            await db.groupmembership.create(
                data={"tenant_id": tenant_id, "group_id": group_id, "member_id": member_id, **data}
            )
    return await group_detail(db, user_id, group_id)


async def _target(db: Prisma, group_id: str, member_id: str):
    membership = await db.groupmembership.find_first(where={"group_id": group_id, "member_id": member_id})
    if membership is None or membership.status == "declined":
        raise NotFoundError("That person isn't in this group")
    return membership


async def change_role(db: Prisma, user_id: str, group_id: str, member_id: str, role_key: str) -> dict:
    me, role = await _my_role(db, user_id, group_id)
    _require(role, "manage_roster")
    if member_id == me["id"]:
        raise ForbiddenError("You can't change your own role", code="self_assignment")
    membership = await _target(db, group_id, member_id)
    current_role = await groups_repository.get_role(db, membership.role)
    new_role = await groups_repository.get_role(db, role_key)
    if new_role is None:
        raise NotFoundError("No such group role")
    if not (_outranks(role, current_role) and _outranks(role, new_role)):
        raise ForbiddenError("You can only manage people and roles below your own", code="group_rank")
    await db.groupmembership.update(where={"id": membership.id}, data={"role": role_key})
    return await group_detail(db, user_id, group_id)


async def remove(db: Prisma, user_id: str, group_id: str, member_id: str) -> dict | None:
    me, role = await _my_role(db, user_id, group_id)
    membership = await _target(db, group_id, member_id)
    if member_id == me["id"]:
        # Anyone may leave a group — except its last leader.
        if (
            role.rank == 0
            and await db.groupmembership.count(where={"group_id": group_id, "role": role.key, "status": "active"}) <= 1
        ):
            raise ConflictError("Hand the group to another leader before you leave")
        await db.groupmembership.delete(where={"id": membership.id})
        return None
    _require(role, "manage_roster")
    target_role = await groups_repository.get_role(db, membership.role)
    if not _outranks(role, target_role):
        raise ForbiddenError("You can only remove people below your own role", code="group_rank")
    await db.groupmembership.delete(where={"id": membership.id})
    return await group_detail(db, user_id, group_id)


async def my_events(db: Prisma, user_id: str) -> list[dict]:
    member = await people_service.get_member_by_user_id(db, user_id)
    return await events_service.events_for_member(db, member["id"] if member else None)
