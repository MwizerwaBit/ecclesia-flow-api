from prisma import Prisma


async def member_facts(db: Prisma) -> list[dict]:
    """The few columns the member reports count. Filtering to the caller's
    branch happens on these rows (it needs the associate units too)."""
    return await db.query_raw(
        """
        select m.status, coalesce(m.joined_at, m.created_at::date) as since, m.unit_id::text as unit_id,
               array(select um.unit_id::text from unit_memberships um
                      where um.member_id = m.id and um.kind = 'associate' and um.status = 'active')
                 as associate_unit_ids
        from members m
        """
    )
