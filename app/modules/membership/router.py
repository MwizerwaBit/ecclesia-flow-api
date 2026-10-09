"""Unit membership endpoints.

Reading a person's memberships needs the person to be visible; each
membership row is itself only visible inside its own unit's scope (RLS), so
a branch sees its own part of someone's history. Changing a membership needs
the membership's unit inside the session's branch; moving someone's home is
for their current home unit's staff, and only to a unit they may place
people in.
"""

from typing import Literal

from fastapi import APIRouter, Depends

from app.core.authz import CurrentScope, ensure_member_visible, ensure_member_writable, ensure_unit_in_scope
from app.core.deps import CurrentClaims, TenantDb, require_permission
from app.core.pagination import PaginatedRoute
from app.modules.membership import service
from app.modules.membership.schemas import (
    AssociateMembershipCreate,
    MembershipStatusChange,
    MoveHomeUnitRequest,
    UnitMembershipRead,
)

router = APIRouter(route_class=PaginatedRoute, tags=["membership"])


@router.get(
    "/members/{member_id}/unit-memberships",
    response_model=list[UnitMembershipRead],
    dependencies=[Depends(require_permission("members:read"))],
)
async def list_unit_memberships(member_id: str, scope: CurrentScope, db: TenantDb):
    await ensure_member_visible(db, scope, member_id)
    return await service.list_for_member(db, member_id)


@router.post(
    "/members/{member_id}/unit-memberships",
    response_model=UnitMembershipRead,
    dependencies=[Depends(require_permission("members:update"))],
)
async def add_associate_membership(
    member_id: str, payload: AssociateMembershipCreate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    await ensure_member_visible(db, scope, member_id)
    await ensure_unit_in_scope(db, scope, payload.unit_id)
    return await service.add_associate(
        db, tenant_id=claims.tenant_id, user_id=claims.sub, member_id=member_id, payload=payload
    )


@router.post(
    "/members/{member_id}/move",
    response_model=list[UnitMembershipRead],
    dependencies=[Depends(require_permission("members:update"))],
)
async def move_home_unit(
    member_id: str, payload: MoveHomeUnitRequest, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    await ensure_member_writable(db, scope, member_id)
    payload.unit_id = scope.assignable_unit(payload.unit_id)
    return await service.move_home(
        db, tenant_id=claims.tenant_id, user_id=claims.sub, member_id=member_id, payload=payload
    )


_ACTIONS: dict[str, str] = {"suspend": "suspended", "reactivate": "active", "end": "ended"}


@router.post(
    "/unit-memberships/{membership_id}/{action}",
    response_model=UnitMembershipRead,
    dependencies=[Depends(require_permission("members:update"))],
)
async def change_membership_status(
    membership_id: str,
    action: Literal["suspend", "reactivate", "end"],
    payload: MembershipStatusChange,
    claims: CurrentClaims,
    scope: CurrentScope,
    db: TenantDb,
):
    membership = await service.get(db, membership_id)
    await ensure_unit_in_scope(db, scope, membership["unit_id"])
    return await service.change_status(
        db,
        tenant_id=claims.tenant_id,
        user_id=claims.sub,
        membership=membership,
        status=_ACTIONS[action],
        reason=payload.reason,
    )
