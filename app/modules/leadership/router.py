from fastapi import APIRouter, Depends, Response

from app.core.authz import CurrentScope
from app.core.deps import CurrentClaims, TenantDb, require_permission
from app.core.pagination import PaginatedRoute
from app.modules.leadership import service
from app.modules.leadership.schemas import AssignHolderInput, PositionCreate, PositionRead, PositionUpdate

router = APIRouter(route_class=PaginatedRoute, prefix="/leadership", tags=["leadership"])

READ = [Depends(require_permission("team:read"))]
MANAGE = [Depends(require_permission("leadership:manage"))]


@router.get("/positions", response_model=list[PositionRead], dependencies=READ)
async def list_positions(db: TenantDb):
    return await service.list_positions(db)


@router.post("/positions", response_model=PositionRead, status_code=201, dependencies=MANAGE)
async def create_position(payload: PositionCreate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    return await service.create_position(db, claims.tenant_id, scope, payload)


@router.patch("/positions/{position_id}", response_model=PositionRead, dependencies=MANAGE)
async def update_position(
    position_id: str, payload: PositionUpdate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    return await service.update_position(db, claims.tenant_id, scope, position_id, payload)


@router.delete("/positions/{position_id}", status_code=204, dependencies=MANAGE)
async def delete_position(position_id: str, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    await service.delete_position(db, claims.tenant_id, scope, position_id)
    return Response(status_code=204)


@router.post("/positions/{position_id}/holders", response_model=PositionRead, dependencies=MANAGE)
async def assign(
    position_id: str, payload: AssignHolderInput, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    return await service.assign(db, claims.tenant_id, scope, position_id, payload.membership_id)


@router.delete("/positions/{position_id}/holders/{membership_id}", status_code=204, dependencies=MANAGE)
async def unassign(position_id: str, membership_id: str, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    await service.unassign(db, claims.tenant_id, scope, position_id, membership_id)
    return Response(status_code=204)
