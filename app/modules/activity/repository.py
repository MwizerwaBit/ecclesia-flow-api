from prisma import Prisma
from prisma.models import AttendanceRecord, Event

_LIST_COLUMNS = """
    e.id, e.title, e.type, e.start_date_time, e.end_date_time, e.location, e.status, e.is_public,
    e.visibility, e.unit_id, e.group_id, e.cover_image_url, e.theme, e.is_recurring, e.recurrence_rule,
    (select count(*) from attendance_records ar where ar.event_id = e.id) as attendee_count,
    (select count(*) from event_reviews r where r.event_id = e.id and r.status = 'pending')::int as pending_reviews
"""


async def list_events(
    db: Prisma, *, search: str | None, upcoming: bool | None, status: str | None = None
) -> list[dict]:
    clauses = []
    params: list = []
    if search:
        params.append(f"%{search.lower()}%")
        clauses.append(f"(lower(e.title) like ${len(params)} or lower(coalesce(e.location, '')) like ${len(params)})")
    if upcoming is True:
        # A repeating event stays "upcoming" while its series continues.
        clauses.append("(e.start_date_time >= now() or e.recurrence_rule is not null)")
    elif upcoming is False:
        clauses.append("e.start_date_time < now() and e.recurrence_rule is null")
    if status:
        params.append(status)
        clauses.append(f"e.status = ${len(params)}")
    where_sql = (" where " + " and ".join(clauses)) if clauses else ""
    return await db.query_raw(
        f"select {_LIST_COLUMNS} from events e {where_sql} order by e.start_date_time asc", *params
    )


async def list_events_for_member(db: Prisma, member_id: str | None) -> list[dict]:
    """Published events a member may see: public and members-only ones, plus
    private (group) events for groups they actively belong to."""
    return await db.query_raw(
        f"""
        select {_LIST_COLUMNS} from events e
        where e.status = 'published'
          and (e.visibility in ('public', 'members')
               or (e.visibility = 'private' and e.group_id in (
                   select gm.group_id from group_memberships gm
                   where gm.member_id = $1::uuid and gm.status = 'active')))
        order by e.start_date_time asc
        """,
        member_id,
    )


async def list_public_events(db: Prisma) -> list[dict]:
    return await db.query_raw(
        """
        select e.id, e.title, e.type, e.description, e.location, e.online_url, e.start_date_time, e.end_date_time,
               e.is_recurring, e.recurrence_rule, e.cover_image_url, e.theme
        from events e
        where e.status = 'published' and e.visibility = 'public'
        order by e.start_date_time asc
        """
    )


async def list_reviews(db: Prisma, event_id: str) -> list[dict]:
    return await db.query_raw(
        """
        select r.reviewer_user_id, u.first_name || ' ' || u.last_name as reviewer_name,
               r.status, r.comment, r.decided_at
        from event_reviews r join users u on u.id = r.reviewer_user_id
        where r.event_id = $1::uuid order by r.created_at
        """,
        event_id,
    )


async def review_queue(db: Prisma, reviewer_user_id: str) -> list[dict]:
    return await db.query_raw(
        f"""
        select {_LIST_COLUMNS} from events e
        join event_reviews r on r.event_id = e.id
        where r.reviewer_user_id = $1::uuid and r.status = 'pending' and e.status = 'pending_review'
        order by e.submitted_at asc nulls last
        """,
        reviewer_user_id,
    )


async def reviewer_candidates(db: Prisma) -> list[dict]:
    """Active team members whose role includes events:review."""
    return await db.query_raw(
        """
        select distinct u.id as user_id, u.first_name || ' ' || u.last_name as name, r.name as role_name, tm.role_id
        from tenant_memberships tm
        join users u on u.id = tm.user_id
        join roles r on r.id = tm.role_id
        where tm.tenant_id = current_tenant_id() and tm.status = 'active'
        order by name
        """
    )


async def get_event_row(db: Prisma, event_id: str) -> dict | None:
    rows = await db.query_raw(
        """
        select e.*, hu.name as unit_name, g.name as group_name
        from events e
        left join hierarchy_units hu on hu.id = e.unit_id
        left join groups g on g.id = e.group_id
        where e.id = $1::uuid
        """,
        event_id,
    )
    return rows[0] if rows else None


async def get_event(db: Prisma, event_id: str) -> Event | None:
    return await db.event.find_unique(where={"id": event_id})


async def create_event(db: Prisma, tenant_id: str, created_by_user_id: str, data: dict) -> Event:
    return await db.event.create(data={**data, "tenant_id": tenant_id, "created_by_user_id": created_by_user_id})


async def update_event(db: Prisma, event_id: str, data: dict) -> Event:
    return await db.event.update(where={"id": event_id}, data=data)


async def count_expected_members(db: Prisma, unit_id: str | None) -> int:
    if unit_id:
        return await db.member.count(where={"status": "active", "unit_id": unit_id})
    return await db.member.count(where={"status": "active"})


async def list_present_members(db: Prisma, event_id: str) -> list[dict]:
    return await db.query_raw(
        """
        select m.id, (m.first_name || ' ' || m.last_name) as name
        from attendance_records ar join members m on m.id = ar.member_id
        where ar.event_id = $1::uuid
        """,
        event_id,
    )


async def list_absent_members(db: Prisma, event_id: str, unit_id: str | None) -> list[dict]:
    unit_clause = "and m.unit_id = $2::uuid" if unit_id else ""
    params = [event_id] + ([unit_id] if unit_id else [])
    return await db.query_raw(
        f"""
        select m.id, (m.first_name || ' ' || m.last_name) as name
        from members m
        where m.status = 'active' {unit_clause}
          and not exists (select 1 from attendance_records ar where ar.event_id = $1::uuid and ar.member_id = m.id)
        """,
        *params,
    )


async def get_attendance_record(db: Prisma, event_id: str, member_id: str) -> AttendanceRecord | None:
    return await db.attendancerecord.find_unique(
        where={"event_id_member_id": {"event_id": event_id, "member_id": member_id}}
    )


async def mark_present(
    db: Prisma, *, tenant_id: str, event_id: str, member_id: str, marked_by_user_id: str
) -> AttendanceRecord:
    return await db.attendancerecord.create(
        data={
            "tenant_id": tenant_id,
            "event_id": event_id,
            "mode": "individual",
            "member_id": member_id,
            "marked_by_user_id": marked_by_user_id,
        }
    )


async def unmark_present(db: Prisma, event_id: str, member_id: str) -> None:
    await db.attendancerecord.delete_many(where={"event_id": event_id, "member_id": member_id})


async def submit_headcount(
    db: Prisma, *, tenant_id: str, event_id: str, adults: int, children: int, total: int, marked_by_user_id: str
) -> AttendanceRecord:
    return await db.attendancerecord.create(
        data={
            "tenant_id": tenant_id,
            "event_id": event_id,
            "mode": "headcount",
            "adult_count": adults,
            "child_count": children,
            "total_count": total,
            "marked_by_user_id": marked_by_user_id,
        }
    )
