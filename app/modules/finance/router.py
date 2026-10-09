from fastapi import APIRouter, Depends, Query

from app.core.authz import CurrentScope, ensure_member_visible
from app.core.deps import CurrentClaims, TenantDb, require_permission
from app.core.pagination import PaginatedRoute
from app.modules.finance import service
from app.modules.finance.schemas import (
    CloseBatchInput,
    ContributionStatement,
    DonationBatchCreate,
    DonationBatchRead,
    DonationEntryForm,
    DonationRead,
    FinanceDashboard,
    FundCreate,
    FundRead,
    GivingReport,
    PledgeRead,
    VoidDonationInput,
)
from app.modules.people import service as people_service

router = APIRouter(route_class=PaginatedRoute, prefix="/finance", tags=["finance"])


@router.get("/dashboard", response_model=FinanceDashboard, dependencies=[Depends(require_permission("finance:read"))])
async def get_dashboard(db: TenantDb):
    return await service.get_dashboard(db)


@router.get("/reports", response_model=GivingReport, dependencies=[Depends(require_permission("finance:read"))])
async def get_giving_report(db: TenantDb, period: str = Query(default="monthly")):
    return await service.get_giving_report(db, period)


@router.get("/funds", response_model=list[FundRead], dependencies=[Depends(require_permission("finance:read"))])
async def list_funds(db: TenantDb):
    return await service.list_funds(db)


@router.post("/funds", response_model=FundRead, dependencies=[Depends(require_permission("finance:update"))])
async def create_fund(payload: FundCreate, claims: CurrentClaims, db: TenantDb):
    return await service.create_fund(db, claims.tenant_id, payload)


@router.get(
    "/batches", response_model=list[DonationBatchRead], dependencies=[Depends(require_permission("finance:read"))]
)
async def list_batches(db: TenantDb, status: str | None = Query(default=None)):
    return await service.list_batches(db, status=status)


@router.get(
    "/batches/{batch_id}", response_model=DonationBatchRead, dependencies=[Depends(require_permission("finance:read"))]
)
async def get_batch(batch_id: str, db: TenantDb):
    return await service.get_batch(db, batch_id)


@router.post("/batches", response_model=DonationBatchRead, dependencies=[Depends(require_permission("finance:create"))])
async def create_batch(payload: DonationBatchCreate, claims: CurrentClaims, db: TenantDb):
    return await service.create_batch(db, claims.tenant_id, claims.sub, payload)


@router.post(
    "/batches/{batch_id}/close",
    response_model=DonationBatchRead,
    dependencies=[Depends(require_permission("finance:update"))],
)
async def close_batch(batch_id: str, payload: CloseBatchInput, db: TenantDb):
    return await service.close_batch(db, batch_id, payload.verified_total)


@router.post(
    "/batches/{batch_id}/post",
    response_model=DonationBatchRead,
    dependencies=[Depends(require_permission("finance:update"))],
)
async def post_batch(batch_id: str, db: TenantDb):
    return await service.post_batch(db, batch_id)


@router.get(
    "/batches/{batch_id}/donations",
    response_model=list[DonationRead],
    dependencies=[Depends(require_permission("finance:read"))],
)
async def list_donations(batch_id: str, db: TenantDb):
    return await service.list_donations(db, batch_id)


@router.post(
    "/batches/{batch_id}/donations",
    response_model=DonationRead,
    dependencies=[Depends(require_permission("finance:create"))],
)
async def create_donation(batch_id: str, payload: DonationEntryForm, claims: CurrentClaims, db: TenantDb):
    return await service.create_donation(db, claims.tenant_id, batch_id, claims.sub, payload)


@router.get("/donations", response_model=list[DonationRead], dependencies=[Depends(require_permission("finance:read"))])
async def list_donations_by_member(db: TenantDb, member_id: str = Query(...)):
    return await service.list_donations_by_member(db, member_id)


@router.post(
    "/donations/{donation_id}/void",
    response_model=DonationRead,
    dependencies=[Depends(require_permission("finance:update"))],
)
async def void_donation(donation_id: str, payload: VoidDonationInput, claims: CurrentClaims, db: TenantDb):
    return await service.void_donation(db, donation_id, reason=payload.reason, voided_by_user_id=claims.sub)


@router.get("/pledges", response_model=list[PledgeRead], dependencies=[Depends(require_permission("finance:read"))])
async def list_pledges(db: TenantDb):
    return await service.list_pledges(db)


@router.get("/statements/{member_id}", response_model=ContributionStatement)
async def get_contribution_statement(
    member_id: str, claims: CurrentClaims, scope: CurrentScope, db: TenantDb, year: int = Query(...)
):
    # Staff/board with finance:export can pull anyone's statement; a member
    # with only giving:read_own can only ever pull their own — resolved by
    # looking up which member record (if any) this login is linked to,
    # never by trusting a role name alone.
    own_member = await people_service.get_member_by_user_id(db, claims.sub)
    is_own = own_member is not None and own_member["id"] == member_id
    if is_own and "giving:read_own" in claims.permissions:
        return await service.generate_contribution_statement(db, member_id, year)
    # Anyone else's statement: the export permission (which also demands an
    # MFA-verified session) plus the usual unit scope on the member.
    await require_permission("finance:export")(claims)
    await ensure_member_visible(db, scope, member_id)
    return await service.generate_contribution_statement(db, member_id, year)
