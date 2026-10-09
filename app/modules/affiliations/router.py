"""Affiliation endpoints. Joining or leaving a parent organisation shapes the
whole church, so changes need ``affiliations:manage`` (the leader's, by
default, and MFA-gated) and a whole-church session."""

from fastapi import APIRouter, Depends

from app.core.authz import CurrentScope
from app.core.deps import CurrentClaims, TenantDb, require_permission
from app.core.exceptions import ForbiddenError
from app.core.pagination import PaginatedRoute
from app.modules.affiliations import service
from app.modules.affiliations.schemas import (
    AffiliateEvent,
    AffiliateSummary,
    AffiliationAccept,
    AffiliationDecision,
    AffiliationGrantsUpdate,
    AffiliationPropose,
    AffiliationRead,
)

router = APIRouter(route_class=PaginatedRoute, prefix="/affiliations", tags=["affiliations"])

READ = [Depends(require_permission("affiliations:read"))]
MANAGE = [Depends(require_permission("affiliations:manage"))]


def _whole_church(scope) -> None:
    if scope.is_scoped:
        raise ForbiddenError("Only whole-church leadership can act for the church", code="out_of_scope")


@router.get("", response_model=list[AffiliationRead], dependencies=READ)
async def list_affiliations(claims: CurrentClaims, db: TenantDb):
    return await service.list_affiliations(db, claims.tenant_id)


@router.post("", response_model=AffiliationRead, dependencies=MANAGE)
async def propose(payload: AffiliationPropose, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    _whole_church(scope)
    return await service.propose(db, tenant_id=claims.tenant_id, user_id=claims.sub, payload=payload)


@router.post("/{affiliation_id}/accept", response_model=AffiliationRead, dependencies=MANAGE)
async def accept(
    affiliation_id: str, payload: AffiliationAccept, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    _whole_church(scope)
    return await service.accept(
        db, tenant_id=claims.tenant_id, user_id=claims.sub, affiliation_id=affiliation_id, payload=payload
    )


@router.post("/{affiliation_id}/decline", response_model=AffiliationRead, dependencies=MANAGE)
async def decline(
    affiliation_id: str, payload: AffiliationDecision, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    _whole_church(scope)
    return await service.decline(
        db, tenant_id=claims.tenant_id, user_id=claims.sub, affiliation_id=affiliation_id, payload=payload
    )


@router.post("/{affiliation_id}/withdraw", response_model=AffiliationRead, dependencies=MANAGE)
async def withdraw(affiliation_id: str, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    _whole_church(scope)
    return await service.withdraw(db, tenant_id=claims.tenant_id, user_id=claims.sub, affiliation_id=affiliation_id)


@router.post("/{affiliation_id}/end", response_model=AffiliationRead, dependencies=MANAGE)
async def end(
    affiliation_id: str, payload: AffiliationDecision, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    _whole_church(scope)
    return await service.end(
        db, tenant_id=claims.tenant_id, user_id=claims.sub, affiliation_id=affiliation_id, payload=payload
    )


@router.put("/{affiliation_id}/grants", response_model=AffiliationRead, dependencies=MANAGE)
async def update_grants(
    affiliation_id: str, payload: AffiliationGrantsUpdate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    _whole_church(scope)
    return await service.update_grants(
        db, tenant_id=claims.tenant_id, user_id=claims.sub, affiliation_id=affiliation_id, payload=payload
    )


# ── What a parent may see of an affiliated church ───────────────────────


@router.get("/{affiliation_id}/summary", response_model=AffiliateSummary, dependencies=READ)
async def affiliate_summary(affiliation_id: str, claims: CurrentClaims, db: TenantDb):
    return await service.summary(db, tenant_id=claims.tenant_id, affiliation_id=affiliation_id)


@router.get("/{affiliation_id}/events", response_model=list[AffiliateEvent], dependencies=READ)
async def affiliate_events(affiliation_id: str, claims: CurrentClaims, db: TenantDb):
    return await service.published_events(db, tenant_id=claims.tenant_id, affiliation_id=affiliation_id)
