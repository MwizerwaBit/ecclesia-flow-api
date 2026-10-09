from prisma import Prisma

from app.core.exceptions import NotFoundError
from app.modules.messaging import repository
from app.modules.messaging.schemas import AnnouncementCreate, AudienceFilter, NotificationPreferencesUpdate


async def list_announcements(db: Prisma, *, status: str | None) -> list[dict]:
    return await repository.list_announcements(db, status=status)


async def get_announcement(db: Prisma, announcement_id: str) -> dict:
    row = await repository.get_announcement_row(db, announcement_id)
    if row is None:
        raise NotFoundError("No such announcement")
    return row


async def create_announcement(db: Prisma, tenant_id: str, author_user_id: str, payload: AnnouncementCreate) -> dict:
    data = payload.model_dump()
    announcement = await repository.create_announcement(db, tenant_id, author_user_id, data)
    return await get_announcement(db, announcement.id)


async def estimate_audience(db: Prisma, filter_: AudienceFilter) -> int:
    return await repository.estimate_audience(db, unit_ids=filter_.unit_ids, statuses=filter_.statuses)


async def list_templates(db: Prisma):
    return await repository.list_templates(db)


async def list_notifications(db: Prisma, user_id: str):
    return await repository.list_notifications(db, user_id)


async def mark_notification_read(db: Prisma, notification_id: str, user_id: str) -> None:
    await repository.mark_notification_read(db, notification_id, user_id)


async def mark_all_read(db: Prisma, user_id: str) -> None:
    await repository.mark_all_read(db, user_id)


_DEFAULT_PREFERENCES = {
    t: {"email": True, "push": True, "in_app": True, "sms": False}
    for t in ("pastoral_alert", "finance_alert", "system", "announcement")
}


async def get_preferences(db: Prisma, user_id: str) -> dict:
    row = await repository.get_preferences(db, user_id)
    if row is not None:
        return row
    return {"user_id": user_id, "preferences": _DEFAULT_PREFERENCES}


async def update_preferences(db: Prisma, user_id: str, payload: NotificationPreferencesUpdate) -> dict:
    return await repository.upsert_preferences(db, user_id, payload.preferences)
