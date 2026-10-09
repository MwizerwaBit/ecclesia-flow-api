from prisma import Prisma
from prisma.models import Member, PastoralNote, SacramentalRecord

from app.core.prisma_utils import coerce_dates

_LIST_ITEM_SQL = """
    select m.id, m.first_name, m.last_name, m.preferred_name, m.photo_url,
           upper(left(m.first_name,1) || left(m.last_name,1)) as initials,
           m.status, m.unit_id, hu.name as unit_name, m.envelope_number, m.last_seen_at,
           m.email, m.phone, m.whatsapp, m.gender, m.joined_at, m.household_id,
           m.id_type, m.national_id_last4,
           array(select um.unit_id from unit_memberships um
                  where um.member_id = m.id and um.kind = 'associate' and um.status = 'active') as associate_unit_ids
    from members m
    left join hierarchy_units hu on hu.id = m.unit_id
"""


async def list_members(db: Prisma, *, search: str | None, status: str | None, unit_id: str | None) -> list[dict]:
    where_clauses = []
    params: list = []
    if search:
        params.append(f"%{search.lower()}%")
        where_clauses.append(
            f"(lower(m.first_name || ' ' || m.last_name) like ${len(params)} "
            f"or lower(coalesce(m.envelope_number, '')) like ${len(params)})"
        )
    if status:
        params.append(status)
        where_clauses.append(f"m.status = ${len(params)}")
    if unit_id:
        params.append(unit_id)
        where_clauses.append(f"m.unit_id = ${len(params)}::uuid")
    where_sql = (" where " + " and ".join(where_clauses)) if where_clauses else ""
    sql = _LIST_ITEM_SQL + where_sql + " order by m.last_name, m.first_name"
    return await db.query_raw(sql, *params)


async def get_not_seen_recently(db: Prisma) -> list[dict]:
    return await db.query_raw(
        _LIST_ITEM_SQL + " where m.last_seen_at is not null and m.last_seen_at <= (now() - interval '28 days') "
        "order by m.last_seen_at asc"
    )


async def get_detail_row(db: Prisma, member_id: str) -> dict | None:
    rows = await db.query_raw(
        """
        select m.*,
               upper(left(m.first_name,1) || left(m.last_name,1)) as initials,
               hu.name as unit_name,
               (select count(*) from attendance_records ar where ar.member_id = m.id) as attended_count,
               (select count(*) from events e where e.attendance_mode = 'individual') as total_individual_events,
               (select coalesce(sum(d.amount), 0) from donations d
                 where d.member_id = m.id and not d.is_voided) as total_giving,
               (select coalesce(sum(d.amount), 0) from donations d where d.member_id = m.id and not d.is_voided
                 and extract(year from d.created_at) = extract(year from now())) as giving_this_year
        from members m
        left join hierarchy_units hu on hu.id = m.unit_id
        where m.id = $1::uuid
        """,
        member_id,
    )
    return rows[0] if rows else None


async def get_detail_row_by_user_id(db: Prisma, user_id: str) -> dict | None:
    rows = await db.query_raw(
        """
        select m.*,
               upper(left(m.first_name,1) || left(m.last_name,1)) as initials,
               hu.name as unit_name,
               (select coalesce(sum(d.amount), 0) from donations d
                 where d.member_id = m.id and not d.is_voided) as total_giving,
               (select coalesce(sum(d.amount), 0) from donations d where d.member_id = m.id and not d.is_voided
                 and extract(year from d.created_at) = extract(year from now())) as giving_this_year
        from members m
        left join hierarchy_units hu on hu.id = m.unit_id
        where m.user_id = $1::uuid
        """,
        user_id,
    )
    return rows[0] if rows else None


async def get_member(db: Prisma, member_id: str) -> Member | None:
    return await db.member.find_unique(where={"id": member_id})


async def create_member(db: Prisma, tenant_id: str, data: dict) -> Member:
    return await db.member.create(data=coerce_dates({**data, "tenant_id": tenant_id}))


async def update_member(db: Prisma, member_id: str, data: dict) -> Member:
    return await db.member.update(where={"id": member_id}, data=coerce_dates(data))


async def list_household_members(db: Prisma, household_id: str) -> list[dict]:
    return await db.query_raw(_LIST_ITEM_SQL + " where m.household_id = $1::uuid", household_id)


async def get_household(db: Prisma, household_id: str) -> dict | None:
    rows = await db.query_raw(
        """
        select h.*,
               (select coalesce(sum(d.amount), 0) from donations d
                 join members hm on hm.id = d.member_id
                 where hm.household_id = h.id and not d.is_voided) as total_giving
        from households h where h.id = $1::uuid
        """,
        household_id,
    )
    return rows[0] if rows else None


async def list_pastoral_notes(db: Prisma, member_id: str) -> list[dict]:
    return await db.query_raw(
        """
        select pn.id, pn.member_id, pn.content, pn.author_user_id,
               u.first_name || ' ' || u.last_name as author_name, pn.is_private, pn.created_at, pn.updated_at
        from pastoral_notes pn join users u on u.id = pn.author_user_id
        where pn.member_id = $1::uuid
        order by pn.created_at desc
        """,
        member_id,
    )


async def create_pastoral_note(
    db: Prisma, *, tenant_id: str, member_id: str, author_user_id: str, content: str, is_private: bool
) -> PastoralNote:
    return await db.pastoralnote.create(
        data={
            "tenant_id": tenant_id,
            "member_id": member_id,
            "author_user_id": author_user_id,
            "content": content,
            "is_private": is_private,
        }
    )


async def list_sacramental_records(db: Prisma, member_id: str) -> list[SacramentalRecord]:
    return await db.sacramentalrecord.find_many(where={"member_id": member_id}, order={"date": "desc"})


async def create_sacramental_record(db: Prisma, *, tenant_id: str, member_id: str, data: dict) -> SacramentalRecord:
    return await db.sacramentalrecord.create(
        data=coerce_dates({**data, "tenant_id": tenant_id, "member_id": member_id})
    )


async def list_visitor_followups(db: Prisma) -> list[dict]:
    return await db.query_raw(
        """
        select m.id as member_id, m.first_name, m.last_name, m.preferred_name, m.photo_url,
               upper(left(m.first_name,1) || left(m.last_name,1)) as initials,
               m.status, hu.name as unit_name, m.envelope_number, m.last_seen_at,
               m.joined_at as visit_date,
               extract(day from now() - m.joined_at)::int as days_since_visit
        from members m
        left join hierarchy_units hu on hu.id = m.unit_id
        where m.status = 'visitor'
        order by m.joined_at asc
        """
    )


async def group_refs_for(db: Prisma, member_ids: list[str]) -> dict[str, list[dict]]:
    """Each member's (non-archived) groups, for the directory's group chips."""
    if not member_ids:
        return {}
    rows = await db.query_raw(
        """
        select gm.member_id, g.id, g.name, g.color, gm.role
        from group_memberships gm
        join groups g on g.id = gm.group_id and not g.is_archived
        where gm.member_id = any($1::uuid[]) and gm.status = 'active'
        order by g.name
        """,
        member_ids,
    )
    out: dict[str, list[dict]] = {}
    for r in rows:
        ref = {"id": r["id"], "name": r["name"], "color": r["color"], "role": r["role"]}
        out.setdefault(r["member_id"], []).append(ref)
    return out


async def find_duplicates(
    db: Prisma,
    *,
    first_name: str | None,
    last_name: str | None,
    phone_digits: str | None,
    email: str | None,
    exclude_id: str | None,
) -> list[dict]:
    """Same name, same phone (last 9 digits, so country-code formatting
    doesn't hide a match) or same email — within this tenant only, via RLS."""
    rows = await db.query_raw(
        _LIST_ITEM_SQL
        + """
        where ($5::uuid is null or m.id <> $5::uuid)
          and (
            ($1::text is not null and $2::text is not null
               and lower(m.first_name) = lower($1) and lower(m.last_name) = lower($2))
            or ($3::text is not null and length($3) >= 7
               and right(regexp_replace(coalesce(m.phone, ''), '[^0-9]', '', 'g'), 9) = right($3, 9))
            or ($4::text is not null and lower(m.email) = lower($4))
          )
        order by m.last_name, m.first_name
        limit 10
        """,
        first_name,
        last_name,
        phone_digits,
        email,
        exclude_id,
    )
    return rows


# Envelope numbers are unique across the church, but a branch-scoped session
# only sees its branch's people — these ask the database functions, which
# answer for the whole church without returning anyone's record.
async def envelope_taken(db: Prisma, envelope: str, exclude_id: str | None = None) -> bool:
    rows = await db.query_raw("select tenant_envelope_taken($1, $2::uuid) as taken", envelope, exclude_id)
    return bool(rows[0]["taken"])


async def next_envelope_number(db: Prisma) -> str:
    rows = await db.query_raw("select tenant_next_envelope_number() as next")
    return str(rows[0]["next"]).zfill(4)


async def household_exists(db: Prisma, household_id: str) -> bool:
    return await db.household.find_unique(where={"id": household_id}) is not None


async def create_household(db: Prisma, tenant_id: str, data: dict):
    return await db.household.create(data={**data, "tenant_id": tenant_id})


async def set_household_head(db: Prisma, household_id: str, member_id: str) -> None:
    await db.execute_raw(
        "update households set head_member_id = $2::uuid where id = $1::uuid and head_member_id is null",
        household_id,
        member_id,
    )


async def list_households(db: Prisma) -> list[dict]:
    return await db.query_raw("select h.* from households h order by h.name")


async def existing_group_ids(db: Prisma, group_ids: list[str]) -> list[str]:
    if not group_ids:
        return []
    rows = await db.query_raw("select id from groups where id = any($1::uuid[]) and not is_archived", group_ids)
    return [r["id"] for r in rows]


async def add_group_memberships(db: Prisma, tenant_id: str, member_id: str, group_ids: list[str]) -> None:
    for group_id in group_ids:
        await db.groupmembership.create(
            data={"tenant_id": tenant_id, "group_id": group_id, "member_id": member_id, "role": "member"}
        )


async def group_units(db: Prisma, group_ids: list[str]) -> dict[str, str | None]:
    if not group_ids:
        return {}
    rows = await db.query_raw("select id, unit_id from groups where id = any($1::uuid[])", group_ids)
    return {r["id"]: r["unit_id"] for r in rows}


async def find_by_id_hash(db: Prisma, id_hash: str) -> dict | None:
    rows = await db.query_raw(
        "select id, first_name, last_name, user_id, unit_id from members where national_id_hash = $1", id_hash
    )
    return rows[0] if rows else None
