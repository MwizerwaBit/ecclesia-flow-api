"""Groups endpoints — RBAC by groups:read / groups:manage, ABAC by unit scope.

A branch-scoped session sees church-wide groups and its own branch's groups,
manages only its own branch's, and only ever sees or adds members of its own
branch on any roster.
"""

from fastapi import APIRouter, Depends, Query, Response

from app.core.authz import (
    CurrentScope,
    ensure_group_readable,
    ensure_group_writable,
    ensure_member_visible,
    ensure_members_visible,
)
from app.core.deps import CurrentClaims, TenantDb, require_permission
from app.core.pagination import PaginatedRoute
from app.modules.groups import service
from app.modules.groups.schemas import (
    AddMembers,
    GroupCreate,
    GroupDetail,
    GroupListItem,
    GroupMembershipRead,
    GroupRoleCreate,
    GroupRoleRead,
    GroupRoleUpdate,
    GroupUpdate,
    MembershipUpdate,
)

router = APIRouter(route_class=PaginatedRoute, prefix="/groups", tags=["groups"])

READ = [Depends(require_permission("groups:read"))]
MANAGE = [Depends(require_permission("groups:manage"))]


# Declared before /{group_id} so "roles" is never read as a group id.
@router.get("/roles", response_model=list[GroupRoleRead], dependencies=READ)
async def list_group_roles(db: TenantDb):
    return await service.list_roles(db)


@router.post("/roles", response_model=GroupRoleRead, status_code=201, dependencies=MANAGE)
async def create_group_role(payload: GroupRoleCreate, claims: CurrentClaims, db: TenantDb):
    return await service.create_role(db, claims.tenant_id, payload)


@router.patch("/roles/{role_id}", response_model=GroupRoleRead, dependencies=MANAGE)
async def update_group_role(role_id: str, payload: GroupRoleUpdate, db: TenantDb):
    return await service.update_role(db, role_id, payload)


@router.delete("/roles/{role_id}", status_code=204, dependencies=MANAGE)
async def delete_group_role(role_id: str, db: TenantDb):
    await service.delete_role(db, role_id)
    return Response(status_code=204)


def _scoped_detail(group: dict, scope) -> dict:
    """Roster entries for out-of-scope members are hidden; counts follow."""
    if not scope.is_scoped:
        return group
    roster = [e for e in group["roster"] if scope.member_visible(e["member"])]
    visible_ids = {e["member_id"] for e in roster}
    return {
        **group,
        "roster": roster,
        "member_count": len(roster),
        "leaders": [leader for leader in group["leaders"] if leader["id"] in visible_ids],
    }


@router.get("", response_model=list[GroupListItem], dependencies=READ)
async def list_groups(db: TenantDb, scope: CurrentScope, include_archived: bool = Query(default=False)):
    return scope.filter_shared(await service.list_groups(db, include_archived=include_archived))


@router.post("", response_model=GroupDetail, status_code=201, dependencies=MANAGE)
async def create_group(payload: GroupCreate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    payload.unit_id = scope.assignable_unit(payload.unit_id)
    return _scoped_detail(await service.create_group(db, claims.tenant_id, payload), scope)


@router.get("/{group_id}", response_model=GroupDetail, dependencies=READ)
async def get_group(group_id: str, db: TenantDb, scope: CurrentScope):
    await ensure_group_readable(db, scope, group_id)
    return _scoped_detail(await service.get_group(db, group_id), scope)


@router.patch("/{group_id}", response_model=GroupDetail, dependencies=MANAGE)
async def update_group(group_id: str, payload: GroupUpdate, scope: CurrentScope, db: TenantDb):
    await ensure_group_writable(db, scope, group_id)
    if "unit_id" in payload.model_fields_set:
        payload.unit_id = scope.assignable_unit(payload.unit_id)
    return _scoped_detail(await service.update_group(db, group_id, payload), scope)


@router.post("/{group_id}/members", response_model=list[GroupMembershipRead], dependencies=MANAGE)
async def add_members(group_id: str, payload: AddMembers, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    await ensure_group_writable(db, scope, group_id)
    payload.member_ids = await ensure_members_visible(db, scope, payload.member_ids)
    return await service.add_members(db, claims.tenant_id, group_id, payload, invited_by_user_id=claims.sub)


@router.patch("/{group_id}/members/{member_id}", response_model=GroupMembershipRead, dependencies=MANAGE)
async def update_membership(
    group_id: str, member_id: str, payload: MembershipUpdate, scope: CurrentScope, db: TenantDb
):
    await ensure_group_writable(db, scope, group_id)
    await ensure_member_visible(db, scope, member_id)
    return await service.update_membership(db, group_id, member_id, payload)


@router.delete("/{group_id}/members/{member_id}", status_code=204, dependencies=MANAGE)
async def remove_member(group_id: str, member_id: str, scope: CurrentScope, db: TenantDb):
    await ensure_group_writable(db, scope, group_id)
    await ensure_member_visible(db, scope, member_id)
    await service.remove_member(db, group_id, member_id)
    return Response(status_code=204)
