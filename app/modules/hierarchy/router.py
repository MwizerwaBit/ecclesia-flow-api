"""Hierarchy endpoints. The whole tree is readable (it is the church's
structure, not personal data); changes are ABAC-limited to the session's own
branch — a branch-scoped admin can add, rename and remove units *below* their
own root, but never edit or delete the root they are scoped to, and never
move anything outside it."""

from fastapi import APIRouter, Depends

from app.core.authz import CurrentScope, ensure_unit_in_scope
from app.core.deps import CurrentClaims, TenantDb, require_any_permission, require_permission
from app.core.exceptions import ForbiddenError
from app.core.pagination import PaginatedRoute
from app.modules.hierarchy import service
from app.modules.hierarchy.schemas import (
    ApplyPresetRequest,
    HierarchyUnitCreate,
    HierarchyUnitRead,
    HierarchyUnitUpdate,
    UnitTypeCreate,
    UnitTypePreset,
    UnitTypeRead,
    UnitTypeUpdate,
)

router = APIRouter(route_class=PaginatedRoute, prefix="/hierarchy", tags=["hierarchy"])

STRUCTURE_READERS = [
    Depends(require_any_permission("hierarchy:read", "members:create", "groups:manage", "events:create"))
]


def _not_own_root(scope, unit_id: str) -> None:
    if scope.is_scoped and unit_id == scope.root_unit_id:
        raise ForbiddenError("Your own branch can only be changed from above it", code="out_of_scope")


# Reading the structure is also needed by anyone who places records in it
# (registering a member, creating a group or event) — reference data, not
# personal data. Changing it still needs hierarchy:update.
@router.get(
    "/units",
    response_model=list[HierarchyUnitRead],
    dependencies=STRUCTURE_READERS,
)
async def list_units(db: TenantDb):
    return await service.list_units(db)


@router.post("/units", response_model=HierarchyUnitRead, dependencies=[Depends(require_permission("hierarchy:update"))])
async def create_unit(payload: HierarchyUnitCreate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    await ensure_unit_in_scope(db, scope, payload.parent_id)
    return await service.create_unit(db, claims.tenant_id, payload)


@router.patch("/units/{unit_id}", status_code=204, dependencies=[Depends(require_permission("hierarchy:update"))])
async def update_unit(unit_id: str, payload: HierarchyUnitUpdate, scope: CurrentScope, db: TenantDb):
    await ensure_unit_in_scope(db, scope, unit_id)
    _not_own_root(scope, unit_id)
    if "parent_id" in payload.model_fields_set:
        await ensure_unit_in_scope(db, scope, payload.parent_id)
    await service.update_unit(db, unit_id, payload)


@router.delete("/units/{unit_id}", status_code=204, dependencies=[Depends(require_permission("hierarchy:update"))])
async def delete_unit(unit_id: str, scope: CurrentScope, db: TenantDb):
    await ensure_unit_in_scope(db, scope, unit_id)
    _not_own_root(scope, unit_id)
    await service.delete_unit(db, unit_id)


# ── Unit types: the church's own names for its levels ───────────────────
# Defining them shapes the whole church, so it's for whole-church staff only.


def _whole_church(scope) -> None:
    if scope.is_scoped:
        raise ForbiddenError("Only whole-church staff can change unit types", code="out_of_scope")


@router.get("/unit-types", response_model=list[UnitTypeRead], dependencies=STRUCTURE_READERS)
async def list_unit_types(db: TenantDb):
    return await service.list_unit_types(db)


@router.get("/unit-type-presets", response_model=list[UnitTypePreset], dependencies=STRUCTURE_READERS)
async def list_unit_type_presets():
    return service.list_presets()


@router.post(
    "/unit-types/apply-preset",
    response_model=list[UnitTypeRead],
    dependencies=[Depends(require_permission("hierarchy:update"))],
)
async def apply_unit_type_preset(payload: ApplyPresetRequest, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    _whole_church(scope)
    return await service.apply_preset(db, claims.tenant_id, payload.preset)


@router.post("/unit-types", response_model=UnitTypeRead, dependencies=[Depends(require_permission("hierarchy:update"))])
async def create_unit_type(payload: UnitTypeCreate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    _whole_church(scope)
    return await service.create_unit_type(db, claims.tenant_id, payload)


@router.patch(
    "/unit-types/{unit_type_id}",
    response_model=UnitTypeRead,
    dependencies=[Depends(require_permission("hierarchy:update"))],
)
async def update_unit_type(unit_type_id: str, payload: UnitTypeUpdate, scope: CurrentScope, db: TenantDb):
    _whole_church(scope)
    return await service.update_unit_type(db, unit_type_id, payload)


@router.delete(
    "/unit-types/{unit_type_id}", status_code=204, dependencies=[Depends(require_permission("hierarchy:update"))]
)
async def delete_unit_type(unit_type_id: str, scope: CurrentScope, db: TenantDb):
    _whole_church(scope)
    await service.delete_unit_type(db, unit_type_id)
