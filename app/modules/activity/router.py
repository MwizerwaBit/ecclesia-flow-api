from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.core.authz import CurrentScope, ensure_event_readable, ensure_event_writable, ensure_member_visible
from app.core.database import set_tenant_context
from app.core.deps import CurrentClaims, PreTenantDb, TenantDb, require_permission
from app.core.exceptions import NotFoundError
from app.core.pagination import PaginatedRoute
from app.modules.activity import service
from app.modules.activity.schemas import (
    AttendanceMarkInput,
    AttendanceRecordRead,
    AttendanceSummary,
    ChurchEventCreate,
    ChurchEventRead,
    ChurchEventUpdate,
    EventListItem,
    HeadcountEntry,
    PublicEvent,
    ReviewDecisionInput,
    ReviewerOption,
    SubmitForReviewInput,
)

router = APIRouter(route_class=PaginatedRoute, prefix="/events", tags=["activity"])


@router.get("", response_model=list[EventListItem], dependencies=[Depends(require_permission("events:read"))])
async def list_events(
    db: TenantDb,
    scope: CurrentScope,
    search: str | None = Query(default=None),
    upcoming: bool | None = Query(default=None),
    status: str | None = Query(default=None),
):
    # Church-wide gatherings are visible to everyone; branch events only in scope.
    return scope.filter_shared(await service.list_events(db, search=search, upcoming=upcoming, status=status))


# Static paths before /{event_id}.
@router.get(
    "/review-queue", response_model=list[EventListItem], dependencies=[Depends(require_permission("events:review"))]
)
async def review_queue(claims: CurrentClaims, db: TenantDb):
    """Gatherings waiting for *my* decision."""
    return await service.review_queue(db, claims.sub)


@router.get(
    "/reviewer-options",
    response_model=list[ReviewerOption],
    dependencies=[Depends(require_permission("events:update"))],
)
async def reviewer_options(claims: CurrentClaims, db: TenantDb):
    return await service.reviewer_options(db, claims.sub)


@router.get("/{event_id}", response_model=ChurchEventRead, dependencies=[Depends(require_permission("events:read"))])
async def get_event(event_id: str, db: TenantDb, scope: CurrentScope):
    await ensure_event_readable(db, scope, event_id)
    return await service.get_event(db, event_id)


@router.post("", response_model=ChurchEventRead, dependencies=[Depends(require_permission("events:create"))])
async def create_event(payload: ChurchEventCreate, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    payload.unit_id = scope.assignable_unit(payload.unit_id)
    return await service.create_event(db, claims.tenant_id, claims.sub, payload)


@router.patch(
    "/{event_id}", response_model=ChurchEventRead, dependencies=[Depends(require_permission("events:update"))]
)
async def update_event(event_id: str, payload: ChurchEventUpdate, scope: CurrentScope, db: TenantDb):
    await ensure_event_writable(db, scope, event_id)
    if "unit_id" in payload.model_fields_set:
        payload.unit_id = scope.assignable_unit(payload.unit_id)
    return await service.update_event(db, event_id, payload)


@router.get(
    "/{event_id}/attendance",
    response_model=AttendanceSummary,
    dependencies=[Depends(require_permission("attendance:read"))],
)
async def get_attendance_summary(event_id: str, db: TenantDb, scope: CurrentScope):
    await ensure_event_readable(db, scope, event_id)
    return await service.get_attendance_summary(db, event_id)


@router.post(
    "/{event_id}/attendance",
    response_model=AttendanceRecordRead,
    dependencies=[Depends(require_permission("attendance:create"))],
)
async def mark_present(
    event_id: str, payload: AttendanceMarkInput, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    await ensure_event_readable(db, scope, event_id)
    await ensure_member_visible(db, scope, payload.member_id)
    payload.event_id = event_id
    return await service.mark_present(db, claims.tenant_id, claims.sub, payload)


@router.delete(
    "/{event_id}/attendance/{member_id}",
    status_code=204,
    dependencies=[Depends(require_permission("attendance:create"))],
)
async def unmark_present(event_id: str, member_id: str, scope: CurrentScope, db: TenantDb):
    await ensure_event_readable(db, scope, event_id)
    await ensure_member_visible(db, scope, member_id)
    await service.unmark_present(db, event_id, member_id)


@router.post(
    "/{event_id}/attendance/headcount",
    response_model=AttendanceRecordRead,
    dependencies=[Depends(require_permission("attendance:create"))],
)
async def submit_headcount(
    event_id: str, payload: HeadcountEntry, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    # A headcount is a figure for the whole gathering: only whoever may edit it.
    await ensure_event_writable(db, scope, event_id)
    payload.event_id = event_id
    return await service.submit_headcount(db, claims.tenant_id, claims.sub, payload)


# ───────────────────────────── Review workflow ─────────────────────────────


@router.post(
    "/{event_id}/submit", response_model=ChurchEventRead, dependencies=[Depends(require_permission("events:update"))]
)
async def submit_for_review(
    event_id: str, payload: SubmitForReviewInput, claims: CurrentClaims, scope: CurrentScope, db: TenantDb
):
    await ensure_event_writable(db, scope, event_id)
    return await service.submit_for_review(db, claims.tenant_id, event_id, claims.sub, payload.reviewer_user_ids)


@router.post(
    "/{event_id}/review", response_model=ChurchEventRead, dependencies=[Depends(require_permission("events:review"))]
)
async def decide_review(event_id: str, payload: ReviewDecisionInput, claims: CurrentClaims, db: TenantDb):
    # ABAC: only a reviewer assigned to this event (checked in the service).
    return await service.decide_review(db, claims.tenant_id, event_id, claims.sub, payload.decision, payload.comment)


@router.post(
    "/{event_id}/publish", response_model=ChurchEventRead, dependencies=[Depends(require_permission("events:review"))]
)
async def publish(event_id: str, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    await ensure_event_writable(db, scope, event_id)
    return await service.publish_directly(db, claims.tenant_id, event_id, claims.sub)


@router.post(
    "/{event_id}/cancel", response_model=ChurchEventRead, dependencies=[Depends(require_permission("events:update"))]
)
async def cancel(event_id: str, claims: CurrentClaims, scope: CurrentScope, db: TenantDb):
    await ensure_event_writable(db, scope, event_id)
    return await service.cancel_event(db, claims.tenant_id, event_id, claims.sub)


@router.get(
    "/{event_id}/occurrences", response_model=list[datetime], dependencies=[Depends(require_permission("events:read"))]
)
async def list_occurrences(
    event_id: str,
    scope: CurrentScope,
    db: TenantDb,
    start: datetime = Query(alias="from"),
    end: datetime = Query(alias="to"),
):
    await ensure_event_readable(db, scope, event_id)
    return await service.occurrences(db, event_id, start, end)


# ───────────────────────────── Public ─────────────────────────────
public_router = APIRouter(route_class=PaginatedRoute, prefix="/churches", tags=["church-directory"])


@public_router.get("/{slug}/events", response_model=list[PublicEvent])
async def public_events(slug: str, db: PreTenantDb):
    """A live church's published, public gatherings — nothing else."""
    org = await db.organization.find_unique(where={"slug": slug})
    if org is None or org.status not in ("trial", "active"):
        raise NotFoundError("No church found at this address")
    # Read this one church's rows under RLS, exactly like a tenant session.
    await set_tenant_context(db, org.id)
    return await service.public_events(db)
