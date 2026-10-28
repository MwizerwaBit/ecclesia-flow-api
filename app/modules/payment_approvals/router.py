"""The platform admin's payment queue: see what churches sent, check the proof,
approve or reject.

Reading needs any platform admin. Deciding needs a *full* platform admin and a
fresh two-step code (X-Step-Up-Token), because approving grants paid access.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.core.deps import PlatformClaims, PlatformDb, require_platform_admin_level, require_step_up
from app.core.pagination import PaginatedRoute
from app.core.rate_limit import limiter
from app.core.responses import attachment_response
from app.modules.billing import manual
from app.modules.billing.schemas import DecisionInput, ManualPaymentAdminRead, ManualStatus

router = APIRouter(route_class=PaginatedRoute, prefix="/platform/payments", tags=["platform-payments"])
DECIDE = [Depends(require_platform_admin_level("full")), Depends(require_step_up)]


@router.get("", response_model=list[ManualPaymentAdminRead])
async def list_payments(db: PlatformDb, status: Annotated[ManualStatus | None, Query()] = None):
    return await manual.list_all(db, status)


@router.get("/pending-count")
async def pending_count(db: PlatformDb) -> dict:
    return {"count": await manual.pending_count(db)}


@router.get("/{payment_id}", response_model=ManualPaymentAdminRead)
async def get_payment(payment_id: str, db: PlatformDb):
    return await manual.get_one(db, payment_id)


@router.get("/{payment_id}/proof")
async def get_proof(payment_id: str, claims: PlatformClaims, db: PlatformDb):
    data, mime = await manual.read_proof(db, claims.sub, payment_id)
    return attachment_response(data, mime, f"proof-{payment_id[:8]}")


@router.post("/{payment_id}/approve", response_model=ManualPaymentAdminRead, dependencies=DECIDE)
@limiter.limit("30/minute")
async def approve(request: Request, payment_id: str, payload: DecisionInput, claims: PlatformClaims, db: PlatformDb):
    return await manual.approve(
        db, claims.sub, payment_id, note=payload.note, accept_amount_difference=payload.accept_amount_difference
    )


@router.post("/{payment_id}/reject", response_model=ManualPaymentAdminRead, dependencies=DECIDE)
@limiter.limit("30/minute")
async def reject(request: Request, payment_id: str, payload: DecisionInput, claims: PlatformClaims, db: PlatformDb):
    return await manual.reject(db, claims.sub, payment_id, reason=payload.note)
