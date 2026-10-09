"""Billing endpoints. Not behind the active-organisation gate: paying is how a
pending church becomes active."""

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentClaims, TenantDb, require_permission
from app.core.pagination import PaginatedRoute
from app.core.rate_limit import limiter
from app.modules.billing import service
from app.modules.billing.schemas import (
    BillingOverview,
    CheckoutInput,
    CheckoutRead,
    DemoCheckoutRead,
    DemoPayInput,
    DemoPayResult,
    PlanRead,
)
from app.modules.identity import repository as identity_repository

router = APIRouter(route_class=PaginatedRoute, prefix="/billing", tags=["billing"])
MANAGE = [Depends(require_permission("billing:manage"))]


@router.get("/plans", response_model=list[PlanRead])
async def list_plans():
    """Public — the pricing page and the onboarding plan picker share it."""
    return service.list_plans()


@router.get("/overview", response_model=BillingOverview, dependencies=MANAGE)
async def overview(claims: CurrentClaims, db: TenantDb):
    return await service.overview(db, claims.tenant_id)


@router.post("/checkout", response_model=CheckoutRead, dependencies=MANAGE)
@limiter.limit("10/minute")
async def create_checkout(request: Request, payload: CheckoutInput, claims: CurrentClaims, db: TenantDb):
    user = await identity_repository.get_user_by_id(db, claims.sub)
    return await service.create_checkout(db, claims.tenant_id, claims.sub, user.email, payload)


@router.get("/demo/checkout/{session_id}", response_model=DemoCheckoutRead, dependencies=MANAGE)
async def demo_checkout(session_id: str, claims: CurrentClaims, db: TenantDb):
    return await service.demo_checkout(db, claims.tenant_id, session_id)


@router.post("/demo/checkout/{session_id}/pay", response_model=DemoPayResult, dependencies=MANAGE)
@limiter.limit("10/minute")
async def demo_pay(request: Request, session_id: str, payload: DemoPayInput, claims: CurrentClaims, db: TenantDb):
    return await service.demo_pay(db, claims.tenant_id, session_id, payload)


@router.post("/webhooks/stripe", include_in_schema=False)
async def stripe_webhook(request: Request):
    """Unauthenticated by design — authenticity comes from the signature,
    which is checked against the raw body before anything is parsed."""
    body = await request.body()
    return await service.process_webhook("stripe", body, request.headers.get("stripe-signature"))
