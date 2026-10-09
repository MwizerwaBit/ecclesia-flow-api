from prisma import Json, Prisma
from prisma.models import Announcement


async def list_announcements(db: Prisma, *, status: str | None) -> list[dict]:
    where_sql = "where a.status = $1" if status else ""
    params = [status] if status else []
    return await db.query_raw(
        f"""
        select a.*, (u.first_name || ' ' || u.last_name) as author_name
        from announcements a join users u on u.id = a.author_user_id
        {where_sql}
        order by a.is_pinned desc, a.created_at desc
        """,
        *params,
    )


async def get_announcement_row(db: Prisma, announcement_id: str) -> dict | None:
    rows = await db.query_raw(
        """
        select a.*, (u.first_name || ' ' || u.last_name) as author_name
        from announcements a join users u on u.id = a.author_user_id
        where a.id = $1::uuid
        """,
        announcement_id,
    )
    return rows[0] if rows else None


async def create_announcement(db: Prisma, tenant_id: str, author_user_id: str, data: dict) -> Announcement:
    if data.get("audience_filter") is not None:
        data["audience_filter"] = Json(data["audience_filter"])
    else:
        data.pop("audience_filter", None)
    return await db.announcement.create(
        data={
            **data,
            "tenant_id": tenant_id,
            "author_user_id": author_user_id,
            "sent_count": None,
            "delivered_count": None,
            "opened_count": None,
        }
    )


async def estimate_audience(db: Prisma, *, unit_ids: list[str] | None, statuses: list[str] | None) -> int:
    clauses = ["m.status != 'inactive'"]
    params: list = []
    if unit_ids:
        params.append(unit_ids)
        clauses.append(f"m.unit_id = any(${len(params)}::uuid[])")
    if statuses:
        params.append(statuses)
        clauses.append(f"m.status = any(${len(params)})")
    where_sql = " and ".join(clauses)
    rows = await db.query_raw(f"select count(*) as n from members m where {where_sql}", *params)
    return rows[0]["n"]


async def list_templates(db: Prisma) -> list:
    return await db.messagetemplate.find_many(order={"created_at": "desc"})


async def list_notifications(db: Prisma, user_id: str) -> list:
    return await db.notification.find_many(where={"user_id": user_id}, order={"created_at": "desc"}, take=100)


async def mark_notification_read(db: Prisma, notification_id: str, user_id: str) -> None:
    await db.notification.update_many(where={"id": notification_id, "user_id": user_id}, data={"is_read": True})


async def mark_all_read(db: Prisma, user_id: str) -> None:
    await db.notification.update_many(where={"user_id": user_id, "is_read": False}, data={"is_read": True})


async def get_preferences(db: Prisma, user_id: str) -> dict | None:
    row = await db.notificationpreference.find_unique(where={"user_id": user_id})
    return {"user_id": row.user_id, "preferences": row.preferences} if row else None


async def upsert_preferences(db: Prisma, user_id: str, preferences: dict) -> dict:
    row = await db.notificationpreference.upsert(
        where={"user_id": user_id},
        data={
            "create": {"user_id": user_id, "preferences": Json(preferences)},
            "update": {"preferences": Json(preferences)},
        },
    )
    return {"user_id": row.user_id, "preferences": row.preferences}
