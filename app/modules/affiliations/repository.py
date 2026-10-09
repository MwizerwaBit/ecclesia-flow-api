from prisma import Prisma

_SQL = """
    select a.*,
           child.display_name as child_name, child.slug as child_slug,
           parent.display_name as parent_name, parent.slug as parent_slug
    from organization_affiliations a
    join organizations child on child.id = a.child_org_id
    join organizations parent on parent.id = a.parent_org_id
"""


async def list_all(db: Prisma) -> list[dict]:
    """Both sides' rows — RLS shows a church only affiliations it is part of."""
    return await db.query_raw(_SQL + " order by a.requested_at desc")


async def get(db: Prisma, affiliation_id: str) -> dict | None:
    rows = await db.query_raw(_SQL + " where a.id = $1::uuid", affiliation_id)
    return rows[0] if rows else None


async def open_for_child(db: Prisma, child_org_id: str) -> dict | None:
    rows = await db.query_raw(
        _SQL + " where a.child_org_id = $1::uuid and a.status in ('requested', 'active')", child_org_id
    )
    return rows[0] if rows else None


async def would_cycle(db: Prisma, child_org_id: str, parent_org_id: str) -> bool:
    rows = await db.query_raw(
        "select affiliation_would_cycle($1::uuid, $2::uuid) as cycle", child_org_id, parent_org_id
    )
    return bool(rows[0]["cycle"])


async def create(db: Prisma, data: dict) -> str:
    row = await db.organizationaffiliation.create(data=data)
    return row.id


async def update(db: Prisma, affiliation_id: str, data: dict) -> None:
    await db.organizationaffiliation.update(where={"id": affiliation_id}, data=data)


async def summary(db: Prisma, affiliation_id: str) -> dict | None:
    rows = await db.query_raw("select * from affiliate_summary($1::uuid)", affiliation_id)
    return rows[0] if rows else None


async def published_events(db: Prisma, affiliation_id: str) -> list[dict]:
    return await db.query_raw("select * from affiliate_published_events($1::uuid)", affiliation_id)
