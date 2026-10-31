from datetime import datetime

from prisma import Json, Prisma
from prisma.models import AttendanceRecord, Event

_LIST_COLUMNS = """
    e.id, e.title, e.type, e.start_date_time, e.end_date_time, e.location, e.status, e.is_public,
    e.visibility, e.unit_id, e.group_id, e.cover_image_url, e.theme, e.is_recurring, e.recurrence_rule,
    e.is_all_day,
    (select count(*) from attendance_records ar where ar.event_id = e.id) as attendee_count,
    (select count(*) from event_reviews r where r.event_id = e.id and r.status = 'pending')::int as pending_reviews
"""

#: What an ordinary member may see: published events that are public or members-only,
#: plus private (group) events for groups they actively belong to. The one place this
#: rule lives; every member-facing read goes through it. `{member}` is a $n::uuid
#: placeholder for the member's id (NULL when the person has no member record).
_MEMBER_VISIBLE = """e.status = 'published'
          and (e.visibility in ('public', 'members')
               or (e.visibility = 'private' and e.group_id in (
                   select gm.group_id from group_memberships gm
                   where gm.member_id = {member} and gm.status = 'active')))"""


async def member_id_for_user(db: Prisma, user_id: str) -> str | None:
    rows = await db.query_raw("select id::text as id from members where user_id = $1::uuid limit 1", user_id)
    return rows[0]["id"] if rows else None


async def church_timezone(db: Prisma) -> str | None:
    """The signed-in (or public-request) church's IANA time zone name."""
    rows = await db.query_raw("select timezone from organizations where id = current_tenant_id()")
    return rows[0]["timezone"] if rows else None


async def event_visible_to_member(db: Prisma, event_id: str, member_id: str | None) -> bool:
    rows = await db.query_raw(
        f"select 1 as ok from events e where e.id = $1::uuid and {_MEMBER_VISIBLE.format(member='$2::uuid')}",
        event_id,
        member_id,
    )
    return bool(rows)


async def list_events(
    db: Prisma,
    *,
    search: str | None,
    upcoming: bool | None,
    status: str | None = None,
    restrict_to_member: bool = False,
    member_id: str | None = None,
) -> list[dict]:
    """`restrict_to_member` limits the list to what that member may see; staff
    (who manage events) are not restricted beyond their unit scope."""
    clauses = []
    params: list = []
    if restrict_to_member:
        params.append(member_id)
        clauses.append(_MEMBER_VISIBLE.format(member=f"${len(params)}::uuid"))
    if search:
        params.append(f"%{search.lower()}%")
        clauses.append(f"(lower(e.title) like ${len(params)} or lower(coalesce(e.location, '')) like ${len(params)})")
    if upcoming is True:
        # Not finished yet; a repeating series is a candidate until the service sees it has no date left.
        clauses.append("(coalesce(e.end_date_time, e.start_date_time) >= now() or e.recurrence_rule is not null)")
    elif upcoming is False:
        # Over: a one-off in the past, or a series (the service keeps only the ones that have ended).
        clauses.append("(coalesce(e.end_date_time, e.start_date_time) < now() or e.recurrence_rule is not null)")
    if status:
        params.append(status)
        clauses.append(f"e.status = ${len(params)}")
    where_sql = (" where " + " and ".join(clauses)) if clauses else ""
    return await db.query_raw(
        f"select {_LIST_COLUMNS} from events e {where_sql} order by e.start_date_time asc, e.id asc", *params
    )


async def list_events_for_member(db: Prisma, member_id: str | None) -> list[dict]:
    """Published events a member may see (see _MEMBER_VISIBLE)."""
    return await db.query_raw(
        f"""
        select {_LIST_COLUMNS} from events e
        where {_MEMBER_VISIBLE.format(member="$1::uuid")}
        order by e.start_date_time asc, e.id asc
        """,
        member_id,
    )


_WINDOW_COLUMNS = """
    e.id, e.title, e.type, e.status, e.visibility, e.unit_id, e.group_id, g.name as group_name, e.location,
    e.start_date_time, e.end_date_time, e.is_all_day, e.is_recurring, e.recurrence_rule,
    e.theme->>'accent_color' as accent_color
"""


async def list_events_in_window(
    db: Prisma,
    *,
    start: datetime,
    end: datetime,
    restrict_to_member: bool,
    member_id: str | None,
    type: str | None = None,
    group_id: str | None = None,
    unit_id: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """Events that can appear in [start, end]: one-offs overlapping it, and every
    series that has begun by `end` (the caller expands each into its dates).
    Canceled events are included so a calendar can show them struck through."""
    params: list = [start, end]
    clauses = [
        "((e.recurrence_rule is null and e.start_date_time < $2::timestamptz"
        " and ((e.end_date_time is null and e.start_date_time >= $1::timestamptz)"
        "      or e.end_date_time > $1::timestamptz))"
        " or (e.recurrence_rule is not null and e.start_date_time < $2::timestamptz))"
    ]
    if restrict_to_member:
        params.append(member_id)
        clauses.append(_MEMBER_VISIBLE.format(member=f"${len(params)}::uuid"))
    for column, value in (("type", type), ("group_id", group_id), ("unit_id", unit_id), ("status", status)):
        if value:
            params.append(value)
            cast = "::uuid" if column.endswith("_id") else ""
            clauses.append(f"e.{column} = ${len(params)}{cast}")
    return await db.query_raw(
        f"""
        select {_WINDOW_COLUMNS} from events e
        left join groups g on g.id = e.group_id
        where {" and ".join(clauses)}
        order by e.start_date_time asc, e.id asc
        """,
        *params,
    )


async def list_public_events_in_window(db: Prisma, *, start: datetime, end: datetime) -> list[dict]:
    return await db.query_raw(
        f"""
        select {_WINDOW_COLUMNS} from events e
        left join groups g on g.id = e.group_id
        where e.status = 'published' and e.visibility = 'public'
          and ((e.recurrence_rule is null and e.start_date_time < $2::timestamptz
                and ((e.end_date_time is null and e.start_date_time >= $1::timestamptz)
                     or e.end_date_time > $1::timestamptz))
               or (e.recurrence_rule is not null and e.start_date_time < $2::timestamptz))
        order by e.start_date_time asc, e.id asc
        """,
        start,
        end,
    )


async def list_public_events(db: Prisma) -> list[dict]:
    return await db.query_raw(
        """
        select e.id, e.title, e.type, e.description, e.location, e.online_url, e.start_date_time, e.end_date_time,
               e.is_recurring, e.recurrence_rule, e.cover_image_url, e.theme, e.is_all_day
        from events e
        where e.status = 'published' and e.visibility = 'public'
        order by e.start_date_time asc, e.id asc
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


async def lock_event(db: Prisma, event_id: str) -> None:
    """Holds the row for the rest of the request's transaction, so two edits of
    the same event run one after the other and the stale-edit check is reliable."""
    await db.query_raw("select 1 from events where id = $1::uuid for update", event_id)


async def _set_all_day(db: Prisma, event_id: str, value: bool) -> None:
    # Written with SQL so this column needs nothing from the generated client's model.
    # It also stamps updated_at, which an edit that only flips this flag would otherwise leave alone.
    await db.execute_raw("update events set is_all_day = $2, updated_at = now() where id = $1::uuid", event_id, value)


async def create_event(db: Prisma, tenant_id: str, created_by_user_id: str, data: dict) -> Event:
    data = dict(data)
    all_day = data.pop("is_all_day", False)
    event = await db.event.create(data={**data, "tenant_id": tenant_id, "created_by_user_id": created_by_user_id})
    if all_day:
        await _set_all_day(db, event.id, True)
    return event


async def update_event(db: Prisma, event_id: str, data: dict) -> Event:
    data = dict(data)
    all_day = data.pop("is_all_day", None)
    event = (
        await db.event.update(where={"id": event_id}, data=data)
        if data
        else await db.event.find_unique(where={"id": event_id})
    )
    if all_day is not None:
        await _set_all_day(db, event_id, bool(all_day))
    return event


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
               e.is_recurring, e.recurrence_rule, e.cover_image_url, e.theme, e.is_all_day
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
