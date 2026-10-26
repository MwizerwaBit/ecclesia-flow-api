"""Recording family relationships (staff) and the parent/guardian's own view.

Staff routes need the ordinary people permissions and the usual unit scope.
The /me routes resolve "me" from the token and then apply the family rule in
service.py: they never take a person id except to pick among the caller's own
dependants.
"""

from fastapi import APIRouter, Depends, Response

from app.core.authz import CurrentScope, ensure_member_visible, ensure_member_writable
from app.core.deps import CurrentClaims, TenantDb, require_any_permission, require_permission
from app.core.pagination import PaginatedRoute
from app.modules.family import service
from app.modules.family.schemas import DependantView, FamilyOverview, RelationshipCreate, RelationshipRead

router = APIRouter(route_class=PaginatedRoute, tags=["family"])
SIGNED_IN = [Depends(require_any_permission("portal:view", "profile:read"))]


@router.get(
    "/members/{member_id}/relationships",
    response_model=list[RelationshipRead],
    dependencies=[Depends(require_permission("members:read"))],
)
async def list_relationships(member_id: str, scope: CurrentScope, db: TenantDb):
    await ensure_member_visible(db, scope, member_id)
    return await service.list_for_member(db, member_id)


@router.post(
    "/members/{member_id}/relationships",
    response_model=list[RelationshipRead],
    status_code=201,
    dependencies=[Depends(require_permission("members:update"))],
)
async def add_relationship(
    member_id: str, payload: RelationshipCreate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    await ensure_member_writable(db, scope, member_id)
    await ensure_member_visible(db, scope, str(payload.related_member_id))
    return await service.add(db, tenant_id=claims.tenant_id, user_id=claims.sub, member_id=member_id, payload=payload)


@router.delete(
    "/members/{member_id}/relationships/{relationship_id}",
    status_code=204,
    dependencies=[Depends(require_permission("members:update"))],
)
async def end_relationship(
    member_id: str, relationship_id: str, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    await ensure_member_writable(db, scope, member_id)
    await service.end(
        db, tenant_id=claims.tenant_id, user_id=claims.sub, member_id=member_id, relationship_id=relationship_id
    )
    return Response(status_code=204)


@router.get("/me/family", response_model=FamilyOverview, dependencies=SIGNED_IN)
async def my_family(claims: CurrentClaims, db: TenantDb):
    return await service.my_family(db, claims.sub)


@router.get("/me/family/{member_id}", response_model=DependantView, dependencies=SIGNED_IN)
async def my_dependant(member_id: str, claims: CurrentClaims, db: TenantDb):
    return await service.dependant_view(db, claims.sub, member_id)
