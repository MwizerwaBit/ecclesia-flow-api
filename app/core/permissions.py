"""ABAC helpers — the unit-scope half of the permission model.

A permission check answers "can this role do X"; these answer "...to a
resource inside the role's assigned branch of the org," using one query
against the closure table rather than an application-layer tree walk
(docs/database-design.md §4).
"""
from prisma import Prisma


async def descendant_unit_ids(db: Prisma, root_unit_id: str) -> set[str]:
    rows = await db.query_raw(
        "select descendant_id from hierarchy_closure where ancestor_id = $1::uuid", root_unit_id
    )
    return {str(row["descendant_id"]) for row in rows}


async def unit_in_scope(db: Prisma, scope_unit_id: str | None, target_unit_id: str | None) -> bool:
    """`scope_unit_id` is None => whole-org access (no ABAC restriction).
    `target_unit_id` is None => an org-wide resource, visible to anyone with
    the underlying permission regardless of scope."""
    if scope_unit_id is None:
        return True
    if target_unit_id is None:
        return False
    if scope_unit_id == target_unit_id:
        return True
    rows = await db.query_raw(
        "select 1 from hierarchy_closure where ancestor_id = $1::uuid and descendant_id = $2::uuid",
        scope_unit_id,
        target_unit_id,
    )
    return len(rows) > 0
