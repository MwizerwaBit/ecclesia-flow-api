from prisma import Json, Prisma
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


# ── Event reviews ────────────────────────────────────────────────────────


async def clear_reviews(db: Prisma, event_id: str) -> None:
    await db.eventreview.delete_many(where={"event_id": event_id})


async def create_review(db: Prisma, *, tenant_id: str, event_id: str, reviewer_user_id: str) -> None:
    await db.eventreview.create(
        data={"tenant_id": tenant_id, "event_id": event_id, "reviewer_user_id": reviewer_user_id}
    )


async def find_review(db: Prisma, event_id: str, reviewer_user_id: str):
    return await db.eventreview.find_first(where={"event_id": event_id, "reviewer_user_id": reviewer_user_id})


async def record_review_decision(db: Prisma, review_id: str, data: dict) -> None:
    await db.eventreview.update(where={"id": review_id}, data=data)


async def count_reviews_not_approved(db: Prisma, event_id: str) -> int:
    return await db.eventreview.count(where={"event_id": event_id, "status": {"not": "approved"}})


# ── Schedule collisions ──────────────────────────────────────────────────
# A gathering's own group isn't scheduled against the rest of the church —
# only against OTHER groups' gatherings, since that's the actual conflict: a
# person in two groups can't be at both. An event with no end time is given a
# 2-hour window for this check only; it isn't written back anywhere.


async def find_colliding_events(
    db: Prisma, *, group_id: str, start: object, end: object, exclude_event_id: str | None
) -> list[dict]:
    return await db.query_raw(
        """
        with my_members as (
            select member_id from group_memberships where group_id = $1::uuid and status = 'active'
        ),
        candidates as (
            select e.id, e.title, e.group_id, g.name as group_name, e.start_date_time, e.end_date_time
            from events e
            join groups g on g.id = e.group_id
            where e.group_id is not null
              and e.group_id != $1::uuid
              and e.status != 'canceled'
              and ($4::uuid is null or e.id != $4::uuid)
              and e.start_date_time < $3::timestamptz
              and coalesce(e.end_date_time, e.start_date_time + interval '2 hours') > $2::timestamptz
        )
        select c.id as event_id, c.title as event_title, c.group_id, c.group_name,
               c.start_date_time, c.end_date_time, count(distinct gm.member_id) as affected_member_count
        from candidates c
        join group_memberships gm on gm.group_id = c.group_id and gm.status = 'active'
        join my_members mm on mm.member_id = gm.member_id
        group by c.id, c.title, c.group_id, c.group_name, c.start_date_time, c.end_date_time
        order by c.start_date_time
        """,
        group_id,
        start,
        end,
        exclude_event_id,
    )


async def key_participants_for(db: Prisma, *, group_ids: list[str]) -> list[dict]:
    """Leaders (rank 0) of either group, among members who belong to both —
    the caller passes exactly the two colliding groups' ids and the
    intersection is computed here, so this only ever returns people the
    clash actually affects, not every leader of either group."""
    return await db.query_raw(
        """
        with shared_members as (
            select member_id
            from group_memberships
            where group_id = any($1::uuid[]) and status = 'active'
            group by member_id
            having count(distinct group_id) = 2
        )
        select m.id as member_id, m.user_id, (m.first_name || ' ' || m.last_name) as name,
               gr.name as role_name, g.name as group_name
        from group_memberships gm
        join shared_members sm on sm.member_id = gm.member_id
        join members m on m.id = gm.member_id
        join groups g on g.id = gm.group_id
        join group_roles gr on gr.tenant_id = gm.tenant_id and gr.key = gm.role
        where gm.group_id = any($1::uuid[]) and gm.status = 'active' and gr.rank = 0
        """,
        group_ids,
    )


async def create_notifications(db: Prisma, rows: list[dict]) -> None:
    for row in rows:
        await db.notification.create(data=row)


# ── Registration forms ──────────────────────────────────────────────────


async def list_registration_fields(db: Prisma, event_id: str) -> list[dict]:
    rows = await db.eventregistrationfield.find_many(where={"event_id": event_id}, order={"position": "asc"})
    return [r.model_dump() for r in rows]


async def replace_registration_fields(db: Prisma, tenant_id: str, event_id: str, fields: list[dict]) -> list[dict]:
    """Full replace: the caller always sends the whole form in display order.
    Safe because responses snapshot each field's label at submission time
    rather than pointing back at this row (see EventRegistrationField).

    Runs directly on the request's own transaction rather than opening a
    nested one — ``db`` here is already inside ``tenant_session``'s
    transaction, and a second ``db.tx()`` on top of it put inserts outside
    the scope where the RLS session variable is visible, tripping
    ``tenant_isolation``'s WITH CHECK."""
    await db.eventregistrationfield.delete_many(where={"event_id": event_id})
    created = []
    for position, field in enumerate(fields):
        row = await db.eventregistrationfield.create(
            data={**field, "tenant_id": tenant_id, "event_id": event_id, "position": position}
        )
        created.append(row.model_dump())
    return created


async def get_public_event_row(db: Prisma, event_id: str) -> dict | None:
    rows = await db.query_raw(
        """
        select e.id, e.title, e.type, e.description, e.location, e.online_url, e.start_date_time, e.end_date_time,
               e.is_recurring, e.recurrence_rule, e.cover_image_url, e.theme
        from events e
        where e.id = $1::uuid and e.status = 'published' and e.visibility = 'public'
        """,
        event_id,
    )
    return rows[0] if rows else None


async def create_registration(db: Prisma, tenant_id: str, event_id: str, data: dict) -> dict:
    row = await db.eventregistration.create(
        data={**data, "answers": Json(data["answers"]), "tenant_id": tenant_id, "event_id": event_id}
    )
    return row.model_dump()


async def list_registrations(db: Prisma, event_id: str) -> list[dict]:
    rows = await db.eventregistration.find_many(where={"event_id": event_id}, order={"created_at": "desc"})
    return [r.model_dump() for r in rows]
