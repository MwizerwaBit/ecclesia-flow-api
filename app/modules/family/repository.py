from prisma import Prisma

#: Both directions in one query, each row seen from `$1`'s side. Spouse rows
#: are stored once, so they appear from either person.
_FOR_MEMBER_SQL = """
    select r.id, r.kind, r.started_on, r.ended_on,
           case when r.member_id = $1::uuid then r.related_member_id else r.member_id end as other_id,
           case when r.member_id = $1::uuid then r.kind
                when r.kind = 'parent' then 'child'
                when r.kind = 'guardian' then 'ward'
                else 'spouse' end as role
    from member_relationships r
    where (r.member_id = $1::uuid or r.related_member_id = $1::uuid)
"""


async def list_for_member(db: Prisma, member_id: str, *, live_only: bool) -> list[dict]:
    rows = await db.query_raw(
        f"""
        select x.id, x.role, x.started_on, x.ended_on, o.id as other_member_id,
               o.first_name || ' ' || o.last_name as other_name, o.photo_url as other_photo_url,
               o.date_of_birth as other_dob
        from ({_FOR_MEMBER_SQL} {"and r.ended_on is null" if live_only else ""}) x
        join members o on o.id = x.other_id
        order by x.ended_on nulls first, x.role, o.last_name, o.first_name
        """,
        member_id,
    )
    return rows


async def get_member_brief(db: Prisma, member_id: str) -> dict | None:
    rows = await db.query_raw(
        "select id, first_name, last_name, date_of_birth, status from members where id = $1::uuid", member_id
    )
    return rows[0] if rows else None


async def live_exists(db: Prisma, member_id: str, related_member_id: str, kind: str) -> bool:
    rows = await db.query_raw(
        """select 1 from member_relationships
           where member_id = $1::uuid and related_member_id = $2::uuid and kind = $3 and ended_on is null""",
        member_id,
        related_member_id,
        kind,
    )
    return bool(rows)


async def has_live_spouse(db: Prisma, member_id: str) -> bool:
    rows = await db.query_raw(
        """select 1 from member_relationships
           where kind = 'spouse' and ended_on is null and (member_id = $1::uuid or related_member_id = $1::uuid)""",
        member_id,
    )
    return bool(rows)


async def create(db: Prisma, *, tenant_id: str, member_id: str, related_member_id: str, kind: str, user_id: str) -> str:
    rows = await db.query_raw(
        """insert into member_relationships (tenant_id, member_id, related_member_id, kind, created_by_user_id)
           values ($1::uuid, $2::uuid, $3::uuid, $4, $5::uuid) returning id""",
        tenant_id,
        member_id,
        related_member_id,
        kind,
        user_id,
    )
    return str(rows[0]["id"])


async def get(db: Prisma, relationship_id: str) -> dict | None:
    rows = await db.query_raw(
        "select id, member_id, related_member_id, kind, ended_on from member_relationships where id = $1::uuid",
        relationship_id,
    )
    return rows[0] if rows else None


async def end(db: Prisma, relationship_id: str, user_id: str) -> None:
    await db.execute_raw(
        "update member_relationships set ended_on = current_date, ended_by_user_id = $2::uuid "
        "where id = $1::uuid and ended_on is null",
        relationship_id,
        user_id,
    )


async def member_group_names(db: Prisma, member_id: str) -> list[str]:
    rows = await db.query_raw(
        """select g.name from group_memberships gm join groups g on g.id = gm.group_id
           where gm.member_id = $1::uuid and gm.status = 'active' order by g.name""",
        member_id,
    )
    return [r["name"] for r in rows]
