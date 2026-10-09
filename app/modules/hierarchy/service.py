"""The church's structure: units, the types a church names them by, and the
rules for which may sit under which.

The database guards the invariants that must never break (same-organisation
parents, no cycles, a correct closure table — see migration 20261009090000);
this service checks them first so people get a clear message instead of a
constraint error, and owns the rules that are configuration (unit types).
"""

from prisma import Prisma
from prisma.models import UnitType

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.modules.hierarchy import repository
from app.modules.hierarchy.presets import PRESETS, PRESETS_BY_KEY
from app.modules.hierarchy.schemas import (
    HierarchyUnitCreate,
    HierarchyUnitUpdate,
    UnitTypeCreate,
    UnitTypePreset,
    UnitTypeUpdate,
)


async def list_units(db: Prisma) -> list[dict]:
    return await repository.list_units(db)


# ── Placement rules ─────────────────────────────────────────────────────


async def _resolve_type(db: Prisma, unit_type_id: str | None, free_text: str | None) -> tuple[str, UnitType | None]:
    """The (display type, unit type) a unit gets. Once a church has defined
    unit types, every new or re-typed unit must use one of them."""
    if unit_type_id:
        unit_type = await repository.get_unit_type(db, unit_type_id)
        if unit_type is None:
            raise NotFoundError("No such unit type")
        return unit_type.label, unit_type
    if await repository.list_unit_types(db):
        raise AppError("Choose one of your church's unit types", code="unit_type_required")
    return (free_text or "").strip(), None


async def _type_of_unit(db: Prisma, unit_id: str) -> UnitType | None:
    unit = await repository.get_unit(db, unit_id)
    if unit is None:
        raise NotFoundError("No such parent unit")
    return await repository.get_unit_type(db, unit.unit_type_id) if unit.unit_type_id else None


def _may_sit_under(unit_type: UnitType, parent_type: UnitType | None) -> bool:
    if not unit_type.allowed_parent_keys:
        return True
    return parent_type is not None and parent_type.key in unit_type.allowed_parent_keys


async def _check_placement(db: Prisma, unit_type: UnitType | None, parent_id: str | None) -> None:
    if unit_type is None:
        if parent_id is not None:
            await _type_of_unit(db, parent_id)  # parent must exist
        return
    if parent_id is None:
        if not unit_type.can_be_root:
            raise AppError(f"A {unit_type.label} can't be at the top of the structure", code="invalid_parent")
        return
    parent_type = await _type_of_unit(db, parent_id)
    if not _may_sit_under(unit_type, parent_type):
        under = parent_type.label if parent_type else "unit without a type"
        raise AppError(f"A {unit_type.label} can't sit under a {under}", code="invalid_parent")


# ── Units ───────────────────────────────────────────────────────────────


async def create_unit(db: Prisma, tenant_id: str, payload: HierarchyUnitCreate) -> dict:
    if await repository.name_exists_under_parent(db, name=payload.name, parent_id=payload.parent_id):
        raise ConflictError("A unit with this name already exists under the same parent")
    type_label, unit_type = await _resolve_type(db, payload.unit_type_id, payload.type)
    await _check_placement(db, unit_type, payload.parent_id)
    unit = await repository.create_unit(
        db,
        tenant_id=tenant_id,
        name=payload.name,
        type_=type_label,
        unit_type_id=unit_type.id if unit_type else None,
        code=payload.code,
        address=payload.address,
        parent_id=payload.parent_id,
    )
    # Re-read rather than hand-assemble: the AFTER INSERT trigger that
    # maintains hierarchy_closure has already run by the time this query
    # executes, so `depth` is correct immediately, not just on next list().
    return await repository.get_unit_with_computed_fields(db, unit.id)


async def update_unit(db: Prisma, unit_id: str, payload: HierarchyUnitUpdate) -> None:
    existing = await repository.get_unit(db, unit_id)
    if existing is None:
        raise NotFoundError("No such hierarchy unit")
    data = payload.model_dump(exclude_unset=True)
    parent_id = data.get("parent_id", existing.parent_id)

    if "name" in data or "parent_id" in data:
        name = data.get("name", existing.name)
        if await repository.name_exists_under_parent(db, name=name, parent_id=parent_id, exclude_id=unit_id):
            raise ConflictError("A unit with this name already exists under the same parent")
    if "parent_id" in data and parent_id is not None:
        if parent_id == unit_id or await repository.is_descendant(db, ancestor_id=unit_id, unit_id=parent_id):
            raise ConflictError("A unit can't be placed under itself or one of its own sub-units", code="cycle")

    retyping = "unit_type_id" in data or ("type" in data and existing.unit_type_id is None)
    if retyping:
        type_label, unit_type = await _resolve_type(db, data.get("unit_type_id"), data.get("type") or existing.type)
        data["type"], data["unit_type_id"] = type_label, unit_type.id if unit_type else None
    else:
        data.pop("type", None)  # a typed unit's label follows its type
        unit_type = await repository.get_unit_type(db, existing.unit_type_id) if existing.unit_type_id else None

    if retyping or "parent_id" in data:
        await _check_placement(db, unit_type, parent_id)
    if retyping and unit_type is not None:
        # Its sub-units must still be allowed under its new type.
        for child in await db.hierarchyunit.find_many(where={"parent_id": unit_id, "unit_type_id": {"not": None}}):
            child_type = await repository.get_unit_type(db, child.unit_type_id)
            if child_type and not _may_sit_under(child_type, unit_type):
                raise AppError(
                    f"{child.name} ({child_type.label}) can't sit under a {unit_type.label}", code="invalid_parent"
                )
    await repository.update_unit(db, unit_id, data)


async def delete_unit(db: Prisma, unit_id: str) -> None:
    existing = await repository.get_unit(db, unit_id)
    if existing is None:
        raise NotFoundError("No such hierarchy unit")
    if await repository.has_children_or_members(db, unit_id):
        raise ConflictError("Move or reassign its sub-units and members before deleting this unit")
    if await repository.has_membership_history(db, unit_id):
        raise ConflictError(
            "People have belonged to this unit, and their history is kept — it can't be deleted",
            code="has_history",
        )
    await repository.delete_unit(db, unit_id)


# ── Unit types ──────────────────────────────────────────────────────────


async def list_unit_types(db: Prisma) -> list[UnitType]:
    return await repository.list_unit_types(db)


def list_presets() -> list[UnitTypePreset]:
    return PRESETS


def _check_parent_keys(keys: list[str], known: set[str]) -> list[str]:
    unknown = sorted(set(keys) - known)
    if unknown:
        raise AppError(f"Unknown unit type(s): {', '.join(unknown)}", code="unknown_unit_type")
    return list(dict.fromkeys(keys))


async def create_unit_type(db: Prisma, tenant_id: str, payload: UnitTypeCreate) -> UnitType:
    existing = {t.key for t in await repository.list_unit_types(db)}
    if payload.key in existing:
        raise ConflictError("A unit type with this key already exists")
    data = payload.model_dump()
    # A type may nest under itself (a cell under a cell).
    data["allowed_parent_keys"] = _check_parent_keys(payload.allowed_parent_keys, existing | {payload.key})
    return await repository.create_unit_type(db, tenant_id, data)


async def update_unit_type(db: Prisma, unit_type_id: str, payload: UnitTypeUpdate) -> UnitType:
    unit_type = await repository.get_unit_type(db, unit_type_id)
    if unit_type is None:
        raise NotFoundError("No such unit type")
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "allowed_parent_keys" in data:
        known = {t.key for t in await repository.list_unit_types(db)}
        data["allowed_parent_keys"] = _check_parent_keys(data["allowed_parent_keys"], known)
    updated = await repository.update_unit_type(db, unit_type_id, data)
    if "label" in data:
        await repository.set_units_type_label(db, unit_type_id, updated.label)
    return updated


async def delete_unit_type(db: Prisma, unit_type_id: str) -> None:
    unit_type = await repository.get_unit_type(db, unit_type_id)
    if unit_type is None:
        raise NotFoundError("No such unit type")
    if await repository.unit_type_in_use(db, unit_type_id):
        raise ConflictError("Units still use this type — change their type first", code="in_use")
    for other in await repository.list_unit_types(db):
        if unit_type.key in other.allowed_parent_keys:
            keys = [k for k in other.allowed_parent_keys if k != unit_type.key]
            await repository.update_unit_type(db, other.id, {"allowed_parent_keys": keys})
    await repository.delete_unit_type(db, unit_type_id)


async def apply_preset(db: Prisma, tenant_id: str, preset_key: str) -> list[UnitType]:
    preset = PRESETS_BY_KEY.get(preset_key)
    if preset is None:
        raise NotFoundError("No such preset")
    if await repository.list_unit_types(db):
        raise ConflictError("This church already has unit types — edit them instead", code="already_configured")
    for t in preset.types:
        await repository.create_unit_type(db, tenant_id, t.model_dump())
    return await repository.list_unit_types(db)
