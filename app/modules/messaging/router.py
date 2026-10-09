from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentClaims, TenantDb, require_permission
from app.core.pagination import PaginatedRoute
from app.modules.messaging import service
from app.modules.messaging.schemas import (
    AnnouncementCreate,
    AnnouncementRead,
    AudienceFilter,
    MessageTemplateRead,
    NotificationPreferencesRead,
    NotificationPreferencesUpdate,
    NotificationRead,
)

router = APIRouter(route_class=PaginatedRoute, tags=["messaging"])


@router.get(
    "/announcements",
    response_model=list[AnnouncementRead],
    dependencies=[Depends(require_permission("announcements:read"))],
)
async def list_announcements(db: TenantDb, status: str | None = Query(default=None)):
    return await service.list_announcements(db, status=status)


@router.get(
    "/announcements/{announcement_id}",
    response_model=AnnouncementRead,
    dependencies=[Depends(require_permission("announcements:read"))],
)
async def get_announcement(announcement_id: str, db: TenantDb):
    return await service.get_announcement(db, announcement_id)


@router.post(
    "/announcements",
    response_model=AnnouncementRead,
    dependencies=[Depends(require_permission("announcements:create"))],
)
async def create_announcement(payload: AnnouncementCreate, claims: CurrentClaims, db: TenantDb):
    return await service.create_announcement(db, claims.tenant_id, claims.sub, payload)


@router.post(
    "/announcements/audience-estimate",
    response_model=int,
    dependencies=[Depends(require_permission("announcements:create"))],
)
async def estimate_audience(payload: AudienceFilter, db: TenantDb):
    return await service.estimate_audience(db, payload)


@router.get(
    "/message-templates",
    response_model=list[MessageTemplateRead],
    dependencies=[Depends(require_permission("announcements:read"))],
)
async def list_templates(db: TenantDb):
    return await service.list_templates(db)


@router.get("/notifications", response_model=list[NotificationRead])
async def list_notifications(claims: CurrentClaims, db: TenantDb):
    return await service.list_notifications(db, claims.sub)


@router.patch("/notifications/{notification_id}", status_code=204)
async def mark_notification_read(notification_id: str, claims: CurrentClaims, db: TenantDb):
    await service.mark_notification_read(db, notification_id, claims.sub)


@router.post("/notifications/mark-all-read", status_code=204)
async def mark_all_read(claims: CurrentClaims, db: TenantDb):
    await service.mark_all_read(db, claims.sub)


@router.get("/notification-preferences", response_model=NotificationPreferencesRead)
async def get_preferences(claims: CurrentClaims, db: TenantDb):
    return await service.get_preferences(db, claims.sub)


@router.put("/notification-preferences", response_model=NotificationPreferencesRead)
async def update_preferences(payload: NotificationPreferencesUpdate, claims: CurrentClaims, db: TenantDb):
    return await service.update_preferences(db, claims.sub, payload)
