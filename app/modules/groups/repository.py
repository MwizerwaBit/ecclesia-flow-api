"""Groups data access. Every query runs inside the request's tenant-scoped
transaction, so RLS already limits each one to the caller's church."""

from prisma import Prisma
from prisma.models import Group, GroupMembership

_GROUP_SQL = """
    select g.*, hu.name as unit_name,
           (select count(*) from group_memberships gm where gm.group_id = g.id and gm.status = 'active')::int
             as member_count,
           (select count(*) from group_memberships gm where gm.group_id = g.id and gm.status = 'invited')::int
             as invited_count
    from groups g
    left join hierarchy_units hu on hu.id = g.unit_id
"""

_ROSTER_SQL = """
    select gm.id, gm.tenant_id, gm.group_id, gm.member_id, gm.role, gm.joined_at, gm.note,
           gm.status, gm.invited_at, gm.responded_at, gr.name as role_name, gr.rank as role_rank,
           m.id as m_id, m.first_name, m.last_name, m.preferred_name, m.photo_url,
           upper(left(m.first_name,1) || left(m.last_name,1)) as initials,
           m.status, m.unit_id, hu.name as unit_name, m.envelope_number, m.last_seen_at,
           m.email, m.phone, m.whatsapp, m.gender, m.joined_at as member_joined_at, m.household_id,
           array(select um.unit_id from unit_memberships um
                  where um.member_id = m.id and um.kind = 'associate' and um.status = 'active') as associate_unit_ids
    from group_memberships gm
    join members m on m.id = gm.member_id
    left join group_roles gr on gr.tenant_id = gm.tenant_id and gr.key = gm.role
    left join hierarchy_units hu on hu.id = m.unit_id
"""


async def list_groups(db: Prisma, *, include_archived: bool) -> list[dict]:
    where = "" if include_archived else " where not g.is_archived"
    return await db.query_raw(_GROUP_SQL + where + " order by g.name")


async def get_group(db: Prisma, group_id: str) -> dict | None:
    rows = await db.query_raw(_GROUP_SQL + " where g.id = $1::uuid", group_id)
    return rows[0] if rows else None


async def leaders_for(db: Prisma, group_ids: list[str]) -> list[dict]:
    if not group_ids:
        return []
    return await db.query_raw(
        """
        select gm.group_id, m.id, m.first_name, m.last_name, m.photo_url,
               upper(left(m.first_name,1) || left(m.last_name,1)) as initials
        from group_memberships gm join members m on m.id = gm.member_id
        where gm.role = 'leader' and gm.status = 'active' and gm.group_id = any($1::uuid[])
        order by m.last_name, m.first_name
        """,
        group_ids,
    )


async def roster(db: Prisma, group_id: str) -> list[dict]:
    return await db.query_raw(
        _ROSTER_SQL
        + """ where gm.group_id = $1::uuid
        order by case gm.status when 'active' then 0 when 'invited' then 1 else 2 end,
                 coalesce(gr.rank, 100), m.last_name, m.first_name""",
        group_id,
    )


async def name_taken(db: Prisma, name: str, exclude_id: str | None = None) -> bool:
    rows = await db.query_raw(
        "select 1 from groups where lower(name) = lower($1) and not is_archived"
        " and ($2::uuid is null or id <> $2::uuid) limit 1",
        name,
        exclude_id,
    )
    return bool(rows)


async def unit_exists(db: Prisma, unit_id: str) -> bool:
    return await db.hierarchyunit.find_unique(where={"id": unit_id}) is not None


async def create_group(db: Prisma, data: dict) -> Group:
    return await db.group.create(data=data)


async def update_group(db: Prisma, group_id: str, data: dict) -> Group:
    return await db.group.update(where={"id": group_id}, data=data)


async def existing_members(db: Prisma, member_ids: list[str]) -> list[dict]:
    if not member_ids:
        return []
    return await db.query_raw("select id, user_id from members where id = any($1::uuid[])", member_ids)


# ── Group roles ──────────────────────────────────────────────────────────


async def list_roles(db: Prisma) -> list:
    return await db.grouprole.find_many(order=[{"rank": "asc"}, {"name": "asc"}])


async def get_role(db: Prisma, key: str):
    return await db.grouprole.find_first(where={"key": key})


async def get_role_by_id(db: Prisma, role_id: str):
    return await db.grouprole.find_unique(where={"id": role_id})


async def role_in_use(db: Prisma, key: str) -> bool:
    return await db.groupmembership.count(where={"role": key}) > 0


async def membership_member_ids(db: Prisma, group_id: str) -> set[str]:
    rows = await db.query_raw("select member_id from group_memberships where group_id = $1::uuid", group_id)
    return {r["member_id"] for r in rows}


async def add_membership(db: Prisma, data: dict) -> GroupMembership:
    return await db.groupmembership.create(data=data)


async def find_membership(db: Prisma, group_id: str, member_id: str) -> GroupMembership | None:
    return await db.groupmembership.find_unique(
        where={"group_id_member_id": {"group_id": group_id, "member_id": member_id}}
    )


async def update_membership(db: Prisma, membership_id: str, data: dict) -> GroupMembership:
    return await db.groupmembership.update(where={"id": membership_id}, data=data)


async def delete_membership(db: Prisma, membership_id: str) -> None:
    await db.groupmembership.delete(where={"id": membership_id})
