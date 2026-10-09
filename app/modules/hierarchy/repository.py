from prisma import Prisma
from prisma.models import HierarchyUnit, UnitType

_UNIT_SQL = """
    select
      hu.id, hu.tenant_id, hu.name, hu.code, hu.type, hu.unit_type_id, ut.key as unit_type_key,
      hu.parent_id, hu.address, hu.created_at,
      parent.name as parent_name,
      coalesce((select max(depth) from hierarchy_closure where descendant_id = hu.id), 0) as depth,
      (select count(*) from members m where m.unit_id = hu.id)::int as member_count,
      (select count(*) from hierarchy_units c where c.parent_id = hu.id)::int as sub_unit_count
    from hierarchy_units hu
    left join hierarchy_units parent on parent.id = hu.parent_id
    left join unit_types ut on ut.id = hu.unit_type_id
"""


async def list_units(db: Prisma) -> list[dict]:
    """One flat list with parent_name/depth/member_count/sub_unit_count already
    resolved — the frontend's HierarchyTreeView builds the nested tree
    client-side from exactly this shape (see architecture doc review).
    member_count counts only the people this session may see (RLS)."""
    return await db.query_raw(_UNIT_SQL + " order by hu.created_at asc")


async def get_unit(db: Prisma, unit_id: str) -> HierarchyUnit | None:
    return await db.hierarchyunit.find_unique(where={"id": unit_id})


async def get_unit_with_computed_fields(db: Prisma, unit_id: str) -> dict | None:
    rows = await db.query_raw(_UNIT_SQL + " where hu.id = $1::uuid", unit_id)
    return rows[0] if rows else None


async def name_exists_under_parent(
    db: Prisma, *, name: str, parent_id: str | None, exclude_id: str | None = None
) -> bool:
    where: dict = {"parent_id": parent_id, "name": {"equals": name, "mode": "insensitive"}}
    if exclude_id:
        where["id"] = {"not": exclude_id}
    existing = await db.hierarchyunit.find_first(where=where)
    return existing is not None


async def create_unit(
    db: Prisma,
    *,
    tenant_id: str,
    name: str,
    type_: str,
    unit_type_id: str | None,
    code: str | None,
    address: str | None,
    parent_id: str | None,
) -> HierarchyUnit:
    return await db.hierarchyunit.create(
        data={
            "tenant_id": tenant_id,
            "name": name,
            "type": type_,
            "unit_type_id": unit_type_id,
            "code": code,
            "address": address,
            "parent_id": parent_id,
        }
    )


async def update_unit(db: Prisma, unit_id: str, data: dict) -> HierarchyUnit:
    return await db.hierarchyunit.update(where={"id": unit_id}, data=data)


async def has_children_or_members(db: Prisma, unit_id: str) -> bool:
    child = await db.hierarchyunit.find_first(where={"parent_id": unit_id})
    if child is not None:
        return True
    member = await db.member.find_first(where={"unit_id": unit_id})
    return member is not None


async def has_membership_history(db: Prisma, unit_id: str) -> bool:
    return await db.unitmembership.find_first(where={"unit_id": unit_id}) is not None


async def is_descendant(db: Prisma, *, ancestor_id: str, unit_id: str) -> bool:
    rows = await db.query_raw(
        "select 1 from hierarchy_closure where ancestor_id = $1::uuid and descendant_id = $2::uuid",
        ancestor_id,
        unit_id,
    )
    return bool(rows)


async def delete_unit(db: Prisma, unit_id: str) -> None:
    await db.hierarchyunit.delete(where={"id": unit_id})


# ── Unit types ──────────────────────────────────────────────────────────


async def list_unit_types(db: Prisma) -> list[UnitType]:
    return await db.unittype.find_many(order=[{"sort_order": "asc"}, {"label": "asc"}])


async def get_unit_type(db: Prisma, unit_type_id: str) -> UnitType | None:
    return await db.unittype.find_unique(where={"id": unit_type_id})


async def create_unit_type(db: Prisma, tenant_id: str, data: dict) -> UnitType:
    return await db.unittype.create(data={**data, "tenant_id": tenant_id})


async def update_unit_type(db: Prisma, unit_type_id: str, data: dict) -> UnitType:
    return await db.unittype.update(where={"id": unit_type_id}, data=data)


async def delete_unit_type(db: Prisma, unit_type_id: str) -> None:
    await db.unittype.delete(where={"id": unit_type_id})


async def unit_type_in_use(db: Prisma, unit_type_id: str) -> bool:
    return await db.hierarchyunit.find_first(where={"unit_type_id": unit_type_id}) is not None


async def set_units_type_label(db: Prisma, unit_type_id: str, label: str) -> None:
    """hierarchy_units.type mirrors the type's label for typed units, so every
    screen that already shows `type` shows the church's own word."""
    await db.hierarchyunit.update_many(where={"unit_type_id": unit_type_id}, data={"type": label})
