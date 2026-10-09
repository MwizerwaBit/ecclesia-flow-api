from datetime import date

from prisma import Prisma

_LIST_SQL = """
    select um.id, um.member_id, um.unit_id, hu.name as unit_name, um.kind, um.status,
           um.started_on, um.ended_on, um.end_reason, um.created_at
    from unit_memberships um
    left join hierarchy_units hu on hu.id = um.unit_id
"""


async def list_for_member(db: Prisma, member_id: str) -> list[dict]:
    """Only rows for units this session may see (RLS)."""
    return await db.query_raw(
        _LIST_SQL
        + """ where um.member_id = $1::uuid
        order by case when um.status in ('active', 'suspended') then 0 else 1 end,
                 um.kind, um.started_on desc, um.created_at desc""",
        member_id,
    )


async def get(db: Prisma, membership_id: str) -> dict | None:
    rows = await db.query_raw(_LIST_SQL + " where um.id = $1::uuid", membership_id)
    return rows[0] if rows else None


async def current_in_unit(db: Prisma, member_id: str, unit_id: str) -> dict | None:
    rows = await db.query_raw(
        _LIST_SQL + " where um.member_id = $1::uuid and um.unit_id = $2::uuid and um.status in ('active', 'suspended')",
        member_id,
        unit_id,
    )
    return rows[0] if rows else None


async def create(
    db: Prisma, *, tenant_id: str, member_id: str, unit_id: str, kind: str, started_on: date | None, user_id: str
) -> str:
    rows = await db.query_raw(
        """
        insert into unit_memberships (tenant_id, member_id, unit_id, kind, status, started_on, created_by_user_id)
        values ($1::uuid, $2::uuid, $3::uuid, $4, 'active', coalesce($5::date, current_date), $6::uuid)
        returning id
        """,
        tenant_id,
        member_id,
        unit_id,
        kind,
        started_on.isoformat() if started_on else None,
        user_id,
    )
    return rows[0]["id"]


async def set_status(db: Prisma, membership_id: str, status: str, reason: str | None) -> None:
    """active/suspended keep the membership open; transferred/ended close it today."""
    await db.execute_raw(
        """
        update unit_memberships
           set status = $2,
               ended_on = case when $2 in ('transferred', 'ended') then greatest(current_date, started_on) end,
               end_reason = case when $2 in ('transferred', 'ended') then $3 else end_reason end,
               updated_at = now()
         where id = $1::uuid
        """,
        membership_id,
        status,
        reason,
    )


async def current_home(db: Prisma, member_id: str) -> dict | None:
    rows = await db.query_raw(
        _LIST_SQL + " where um.member_id = $1::uuid and um.kind = 'home' and um.status in ('active', 'suspended')",
        member_id,
    )
    return rows[0] if rows else None


async def set_member_home_unit(db: Prisma, member_id: str, unit_id: str) -> None:
    await db.execute_raw(
        "update members set unit_id = $2::uuid, updated_at = now() where id = $1::uuid", member_id, unit_id
    )
