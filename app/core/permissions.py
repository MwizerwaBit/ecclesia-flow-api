"""ABAC helpers — the unit-scope half of the permission model.

A permission check answers "can this role do X"; these answer "...to a
resource inside the role's assigned branch of the org," using one query
against the closure table rather than an application-layer tree walk
(docs/database-design.md §4).
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def descendant_unit_ids(db: AsyncSession, root_unit_id: str) -> set[str]:
    result = await db.execute(
        text("select descendant_id from hierarchy_closure where ancestor_id = :root"),
        {"root": root_unit_id},
    )
    return {str(row[0]) for row in result.all()}


async def unit_in_scope(db: AsyncSession, scope_unit_id: str | None, target_unit_id: str | None) -> bool:
    """`scope_unit_id` is None => whole-org access (no ABAC restriction).
    `target_unit_id` is None => an org-wide resource, visible to anyone with
    the underlying permission regardless of scope."""
    if scope_unit_id is None:
        return True
    if target_unit_id is None:
        return False
    if scope_unit_id == target_unit_id:
        return True
    result = await db.execute(
        text(
            "select 1 from hierarchy_closure "
            "where ancestor_id = :scope and descendant_id = :target"
        ),
        {"scope": scope_unit_id, "target": target_unit_id},
    )
    return result.first() is not None
