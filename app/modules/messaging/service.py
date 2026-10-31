from prisma import Prisma

from app.core.exceptions import NotFoundError
from app.modules.messaging import repository
from app.modules.messaging.schemas import AnnouncementCreate, AudienceFilter


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
