"""Billing endpoints. Not behind the active-organisation gate: paying is how a
pending church becomes active."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile

from app.core.deps import CurrentAccess, CurrentClaims, TenantDb, require_permission
from app.core.pagination import PaginatedRoute
from app.core.rate_limit import limiter
from app.modules.billing import manual, service
from app.modules.billing.schemas import (
    AccessRead,
    BillingOverview,
    CheckoutInput,
    CheckoutRead,
    DemoCheckoutRead,
    DemoPayInput,
    DemoPayResult,
    ManualPaymentRead,
    PaymentInstructions,
    PlanRead,
)

router = APIRouter(route_class=PaginatedRoute, prefix="/billing", tags=["billing"])
MANAGE = [Depends(require_permission("billing:manage"))]


@router.get("/plans", response_model=list[PlanRead])
async def list_plans():
    """Public — the pricing page and the onboarding plan picker share it."""
    return service.list_plans()


@router.get("/access", response_model=AccessRead | None)
async def my_access(access: CurrentAccess, db: TenantDb):
    """What this church may do right now. Open to every signed-in member of the
    church (not just billing managers), because the whole app greys itself out
    by it. Null for a platform admin, who has no church."""
    return await service.access_for(db, access)


@router.get("/overview", response_model=BillingOverview, dependencies=MANAGE)
async def overview(claims: CurrentClaims, db: TenantDb):
    return await service.overview(db, claims.tenant_id)


@router.post("/checkout", response_model=CheckoutRead, dependencies=MANAGE)
@limiter.limit("10/minute")
async def create_checkout(request: Request, payload: CheckoutInput, claims: CurrentClaims, db: TenantDb):
    return await service.create_checkout(db, claims.tenant_id, claims.sub, payload)


@router.get("/manual/instructions", response_model=PaymentInstructions, dependencies=MANAGE)
async def manual_instructions(
    tier: str, months: Annotated[int, Query(ge=1, le=manual.MAX_MONTHS)], currency: str | None = None
):
    """Where to send the money, and how much, for the plan and months chosen."""
    return manual.instructions(tier, months, currency)


@router.get("/manual/payments", response_model=list[ManualPaymentRead], dependencies=MANAGE)
async def my_manual_payments(db: TenantDb):
    return await manual.list_for_church(db)


@router.post("/manual/payments", response_model=ManualPaymentRead, status_code=201, dependencies=MANAGE)
@limiter.limit("6/hour")
async def submit_manual_payment(
    request: Request,
    claims: CurrentClaims,
    db: TenantDb,
    tier: Annotated[str, Form()],
    months: Annotated[int, Form(ge=1, le=manual.MAX_MONTHS)],
    sender_name: Annotated[str, Form()],
    sent_amount_cents: Annotated[int, Form(gt=0)],
    proof: Annotated[UploadFile, File()],
    momo_transaction_id: Annotated[str | None, Form()] = None,
    currency: Annotated[str | None, Form()] = None,
):
    return await manual.submit(
        db,
        org_id=claims.tenant_id,
        user_id=claims.sub,
        tier=tier,
        months=months,
        sender_name=sender_name,
        sent_amount_cents=sent_amount_cents,
        momo_transaction_id=momo_transaction_id,
        proof=await proof.read(),
        currency=currency,
    )


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
