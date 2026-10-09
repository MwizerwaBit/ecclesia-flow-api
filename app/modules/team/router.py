from fastapi import APIRouter, Depends

from app.core.authz import CurrentScope
from app.core.deps import CurrentClaims, TenantDb, require_permission, require_step_up
from app.core.exceptions import ForbiddenError
from app.core.pagination import PaginatedRoute
from app.modules.team import service
from app.modules.team.schemas import (
    InviteStaffPayload,
    LeadershipTransferRead,
    RequestLeadershipTransferPayload,
    TeamMemberRead,
    UpdateAssignmentPayload,
)

router = APIRouter(route_class=PaginatedRoute, prefix="/team", tags=["team"])


@router.get("", response_model=list[TeamMemberRead], dependencies=[Depends(require_permission("team:read"))])
async def list_team(db: TenantDb):
    return await service.list_team(db)


@router.get("/leader", response_model=TeamMemberRead | None, dependencies=[Depends(require_permission("team:read"))])
async def get_leader(db: TenantDb):
    return await service.get_leader(db)


@router.post("/invite", response_model=TeamMemberRead, dependencies=[Depends(require_permission("team:invite"))])
async def invite(payload: InviteStaffPayload, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    return await service.invite(db, claims.tenant_id, scope, payload)


@router.patch(
    "/{membership_id}", response_model=TeamMemberRead, dependencies=[Depends(require_permission("team:invite"))]
)
async def update_assignment(
    membership_id: str, payload: UpdateAssignmentPayload, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    return await service.update_assignment(db, membership_id, scope, claims.membership_id, payload)


@router.get(
    "/leadership-transfer",
    response_model=LeadershipTransferRead | None,
    dependencies=[Depends(require_permission("team:read"))],
)
async def get_leadership_transfer(claims: CurrentClaims, db: TenantDb):
    return await service.get_leadership_transfer(db, claims.tenant_id)


@router.post(
    "/leadership-transfer",
    response_model=LeadershipTransferRead,
    dependencies=[Depends(require_step_up)],
)
async def request_leadership_transfer(payload: RequestLeadershipTransferPayload, claims: CurrentClaims, db: TenantDb):
    return await service.request_leadership_transfer(db, claims.tenant_id, claims.sub, payload.nominee_id)


@router.post(
    "/leadership-transfer/{transfer_id}/approve",
    response_model=LeadershipTransferRead,
    dependencies=[Depends(require_step_up)],
)
async def approve_leadership_transfer(transfer_id: str, claims: CurrentClaims, db: TenantDb):
    if not claims.membership_id:
        raise ForbiddenError("No active membership on this session")
    return await service.approve_leadership_transfer(db, claims.tenant_id, transfer_id, claims.membership_id)


@router.delete("/leadership-transfer/{transfer_id}", status_code=204, dependencies=[Depends(require_step_up)])
async def cancel_leadership_transfer(transfer_id: str, claims: CurrentClaims, db: TenantDb):
    await service.cancel_leadership_transfer(db, transfer_id, claims.sub)
