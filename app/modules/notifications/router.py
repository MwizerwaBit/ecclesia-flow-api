"""The signed-in user's own notifications and preferences.

Every route resolves "me" from the token (tenant and user id); there is no
way to ask for someone else's. Always-on in app/api.py: a pending or
suspended church's people still need to see "your payment was rejected".
Reading and managing your own notifications is one permission,
``notifications:read``, which every system role holds.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.core.authz import CurrentScope
from app.core.deps import CurrentClaims, TenantDb, require_permission
from app.core.pagination import PageParams, PaginatedRoute, page_headers, paged_in_sql
from app.modules.notifications import broadcast, service
from app.modules.notifications.schemas import (
    Audience,
    AudienceEstimate,
    BroadcastCreate,
    BroadcastResult,
    NotificationPreferencesRead,
    NotificationPreferencesUpdate,
    NotificationRead,
    NotificationType,
    NotificationUpdate,
    UnreadCount,
)

router = APIRouter(
    route_class=PaginatedRoute,
    tags=["notifications"],
    dependencies=[Depends(require_permission("notifications:read"))],
)


@router.get("/notifications", response_model=list[NotificationRead])
@paged_in_sql
async def list_notifications(
    claims: CurrentClaims,
    db: TenantDb,
    request: Request,
    response: Response,
    page: Annotated[PageParams, Depends()],
    status: Literal["all", "unread"] = "all",
    type_: Annotated[NotificationType | None, Query(alias="type")] = None,
):
    rows, total = await service.list_notifications(
        db,
        claims.tenant_id,
        claims.sub,
        unread_only=status == "unread",
        type_=type_,
        limit=page.limit,
        offset=page.offset,
    )
    page_headers(request, response, page, total)
    return rows


@router.get("/notifications/unread-count", response_model=UnreadCount)
async def unread_count(claims: CurrentClaims, db: TenantDb):
    return {"count": await service.unread_count(db, claims.tenant_id, claims.sub)}


@router.post("/notifications/mark-all-read", status_code=204)
async def mark_all_read(claims: CurrentClaims, db: TenantDb):
    await service.mark_all_read(db, claims.tenant_id, claims.sub)


@router.get("/notifications/{notification_id}", response_model=NotificationRead)
async def get_notification(notification_id: UUID, claims: CurrentClaims, db: TenantDb):
    return await service.get_notification(db, claims.tenant_id, claims.sub, str(notification_id))


@router.patch("/notifications/{notification_id}", response_model=NotificationRead)
async def set_read(
    notification_id: UUID,
    claims: CurrentClaims,
    db: TenantDb,
    payload: NotificationUpdate = NotificationUpdate(),  # noqa: B008 — immutable default; no body = "mark read"
):
    return await service.set_read(db, claims.tenant_id, claims.sub, str(notification_id), payload.is_read)


@router.get("/notification-preferences", response_model=NotificationPreferencesRead)
async def get_preferences(claims: CurrentClaims, db: TenantDb):
    return await service.get_preferences(db, claims.sub)


@router.put("/notification-preferences", response_model=NotificationPreferencesRead)
async def update_preferences(payload: NotificationPreferencesUpdate, claims: CurrentClaims, db: TenantDb):
    return await service.update_preferences(db, claims.sub, payload)


# Sending is a business feature, unlike reading your own notifications: it is mounted
# separately in app/api.py (needs an active church and the communications module) and
# gated by its own permission.
send_router = APIRouter(
    tags=["notifications"],
    dependencies=[Depends(require_permission("notifications:send"))],
)


@send_router.post("/notifications/send/audience-estimate", response_model=AudienceEstimate)
async def estimate_audience(payload: Audience, db: TenantDb, scope: CurrentScope):
    return await broadcast.estimate_audience(db, scope, payload)


@send_router.post("/notifications/send", response_model=BroadcastResult)
async def send_notification(payload: BroadcastCreate, claims: CurrentClaims, db: TenantDb, scope: CurrentScope):
    return await broadcast.send(db, scope, claims.tenant_id, claims.sub, payload)
