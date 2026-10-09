from fastapi import APIRouter, Depends

from app.core.authz import CurrentScope, ensure_grantable
from app.core.deps import CurrentClaims, TenantDb, require_permission
from app.core.pagination import PaginatedRoute
from app.modules.rbac import service
from app.modules.rbac.schemas import CustomRoleCreate, CustomRoleRead, PermissionCatalogueModule

router = APIRouter(route_class=PaginatedRoute, prefix="/roles", tags=["rbac"])


@router.get("/catalogue", response_model=list[PermissionCatalogueModule])
async def permission_catalogue(_claims: CurrentClaims):
    return service.permission_catalogue()


@router.get("", response_model=list[CustomRoleRead], dependencies=[Depends(require_permission("roles:read"))])
async def list_roles(db: TenantDb):
    return await service.list_roles(db)


@router.post("", response_model=CustomRoleRead, dependencies=[Depends(require_permission("roles:create"))])
async def create_role(payload: CustomRoleCreate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    # Nobody can mint a role more powerful than themselves.
    ensure_grantable(scope, payload.permissions)
    return await service.create_role(
        db, tenant_id=claims.tenant_id, name=payload.name, color=payload.color, permissions=payload.permissions
    )
