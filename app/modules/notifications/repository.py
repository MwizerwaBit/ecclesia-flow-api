import json

from prisma import Json, Prisma

_COLUMNS = (
    "id, tenant_id, user_id, type, title, body, is_read, read_at, action_url, resource_type, resource_id, created_at"
)


async def list_notifications(
    db: Prisma, tenant_id: str, user_id: str, *, unread_only: bool, type_: str | None, limit: int, offset: int
) -> tuple[list[dict], int]:
    """One page of a user's notifications, newest first (id breaks ties so pages
    never overlap), and the total across all pages. Both filters narrow the
    user's own rows; they never widen them."""
    clauses = ["tenant_id = $1::uuid", "user_id = $2::uuid"]
    params: list = [tenant_id, user_id]
    if unread_only:
        clauses.append("not is_read")
    if type_:
        params.append(type_)
        clauses.append(f"type = ${len(params)}")
    where_sql = " and ".join(clauses)
    rows = await db.query_raw(
        f"""
        select {_COLUMNS} from notifications
        where {where_sql}
        order by created_at desc, id desc
        limit ${len(params) + 1} offset ${len(params) + 2}
        """,
        *params,
        limit,
        offset,
    )
    total = await db.query_raw(f"select count(*)::int as n from notifications where {where_sql}", *params)
    return rows, total[0]["n"]


async def unread_count(db: Prisma, tenant_id: str, user_id: str) -> int:
    rows = await db.query_raw(
        "select count(*)::int as n from notifications"
        " where tenant_id = $1::uuid and user_id = $2::uuid and not is_read",
        tenant_id,
        user_id,
    )
    return rows[0]["n"]


async def get_notification(db: Prisma, tenant_id: str, user_id: str, notification_id: str) -> dict | None:
    rows = await db.query_raw(
        f"select {_COLUMNS} from notifications where id = $3::uuid and tenant_id = $1::uuid and user_id = $2::uuid",
        tenant_id,
        user_id,
        notification_id,
    )
    return rows[0] if rows else None


async def set_read(db: Prisma, tenant_id: str, user_id: str, notification_id: str, is_read: bool) -> dict | None:
    """One statement, so two devices marking it at once can't interleave. Marking
    an already-read row keeps its original read time; marking unread clears it."""
    rows = await db.query_raw(
        f"""
        update notifications
        set is_read = $4::boolean,
            read_at = case when $4::boolean then coalesce(read_at, now()) end
        where id = $3::uuid and tenant_id = $1::uuid and user_id = $2::uuid
        returning {_COLUMNS}
        """,
        tenant_id,
        user_id,
        notification_id,
        is_read,
    )
    return rows[0] if rows else None


async def mark_all_read(db: Prisma, tenant_id: str, user_id: str) -> int:
    return await db.execute_raw(
        """
        update notifications set is_read = true, read_at = now()
        where tenant_id = $1::uuid and user_id = $2::uuid and not is_read
        """,
        tenant_id,
        user_id,
    )


async def eligible_recipients(db: Prisma, tenant_id: str, user_ids: list[str]) -> list[str]:
    """Which of these users can be notified in this church: an active login with
    an active membership here. Anyone else (another church's user, a suspended
    member, an id that doesn't exist) is simply absent from the answer."""
    rows = await db.query_raw(
        """
        select u.id from users u
        join tenant_memberships tm on tm.user_id = u.id
        where tm.tenant_id = $1::uuid and tm.status = 'active' and u.status = 'active'
          and u.id = any($2::uuid[])
        """,
        tenant_id,
        user_ids,
    )
    return [str(r["id"]) for r in rows]


async def stored_preferences(db: Prisma, user_ids: list[str]) -> dict[str, dict]:
    rows = await db.notificationpreference.find_many(where={"user_id": {"in": user_ids}})
    return {r.user_id: r.preferences for r in rows}


async def insert_notifications(
    db: Prisma,
    tenant_id: str,
    user_ids: list[str],
    *,
    type_: str,
    title: str,
    body: str,
    action_url: str | None,
    resource_type: str | None,
    resource_id: str | None,
    actor_user_id: str | None,
    dedupe_key: str | None,
) -> list[dict]:
    """One row per user in a single statement. A user who already has a
    notification with this dedupe key is skipped by the partial unique index
    (ON CONFLICT DO NOTHING); returns the rows actually created (id, user_id)."""
    rows = await db.query_raw(
        """
        insert into notifications
            (id, tenant_id, user_id, type, title, body, action_url, resource_type, resource_id,
             actor_user_id, dedupe_key)
        select gen_random_uuid(), $1::uuid, u, $2, $3, $4, $5, $6, $7::uuid, $8::uuid, $9
        from unnest($10::uuid[]) as u
        on conflict (tenant_id, user_id, dedupe_key) where dedupe_key is not null do nothing
        returning id, user_id
        """,
        tenant_id,
        type_,
        title,
        body,
        action_url,
        resource_type,
        resource_id,
        actor_user_id,
        dedupe_key,
        user_ids,
    )
    return [{"id": str(r["id"]), "user_id": str(r["user_id"])} for r in rows]


async def queue_email_deliveries(db: Prisma, tenant_id: str, notification_ids: list[str], user_ids: list[str]) -> int:
    """Pending email deliveries for just-created notifications, written in the
    same transaction as the notifications themselves: if the action rolls back,
    so does the email. The worker (delivery.py) sends from committed rows only."""
    return await db.execute_raw(
        """
        insert into notification_deliveries (tenant_id, notification_id, user_id, channel, status, next_attempt_at)
        select $1::uuid, t.n, t.u, 'email', 'pending', now()
        from unnest($2::uuid[], $3::uuid[]) as t(n, u)
        on conflict (notification_id, channel) do nothing
        """,
        tenant_id,
        notification_ids,
        user_ids,
    )


async def claim_due_email_deliveries(db: Prisma, limit: int, lease_seconds: int) -> list[dict]:
    """Take up to `limit` due email deliveries for this worker. FOR UPDATE SKIP
    LOCKED lets several workers run at once without taking the same row, and the
    claim pushes next_attempt_at out by the lease so a worker that dies mid-send
    leaves the row to be retried, not stuck. Counts the attempt up front."""
    return await db.query_raw(
        """
        with due as (
            select id from notification_deliveries
            where channel = 'email' and status = 'pending' and next_attempt_at <= now()
            order by next_attempt_at, id
            limit $1::int
            for update skip locked
        ),
        claimed as (
            update notification_deliveries d
            set attempts = d.attempts + 1,
                next_attempt_at = now() + make_interval(secs => $2::double precision),
                updated_at = now()
            from due
            where d.id = due.id
            returning d.id, d.tenant_id, d.notification_id, d.user_id, d.attempts
        )
        select c.id, c.attempts, c.user_id, n.type, n.title, n.body, n.action_url,
               u.email, (u.status = 'active') as user_active,
               exists (
                   select 1 from tenant_memberships tm
                   where tm.user_id = c.user_id and tm.tenant_id = c.tenant_id and tm.status = 'active'
               ) as member_active,
               o.display_name
        from claimed c
        join notifications n on n.id = c.notification_id
        join users u on u.id = c.user_id
        join organizations o on o.id = c.tenant_id
        order by c.id
        """,
        limit,
        float(lease_seconds),
    )


async def finish_delivery(
    db: Prisma, delivery_id: str, status: str, error: str | None, retry_in_seconds: int | None = None
) -> None:
    """Record how an attempt ended: sent, skipped, failed (gave up), or pending
    again with the next attempt `retry_in_seconds` away."""
    await db.execute_raw(
        """
        update notification_deliveries
        set status = $2::text,
            last_error = $3,
            sent_at = case when $2::text = 'sent' then now() else sent_at end,
            next_attempt_at = case when $2::text = 'pending'
                                   then now() + make_interval(secs => $4::double precision) end,
            updated_at = now()
        where id = $1::uuid
        """,
        delivery_id,
        status,
        error,
        float(retry_in_seconds or 0),
    )


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


# ── Manual notifications ────────────────────────────────────────────────


async def audience_members(db: Prisma, *, unit_ids: list[str], statuses: list[str]) -> list[dict]:
    """The members a send would reach. Read under the sender's own session, so a
    branch-scoped sender's unit-scope RLS already limits this to their branch."""
    clauses = ["m.status <> 'inactive'"]
    params: list = []
    if unit_ids:
        params.append(unit_ids)
        clauses.append(f"m.unit_id = any(${len(params)}::uuid[])")
    if statuses:
        params.append(statuses)
        clauses.append(f"m.status = any(${len(params)}::text[])")
    return await db.query_raw(f"select m.id, m.user_id from members m where {' and '.join(clauses)}", *params)


_BROADCAST_COLUMNS = "id, audience_size, without_login, notified, opted_out, skipped"


async def find_broadcast(db: Prisma, tenant_id: str, sender_user_id: str, request_key: str) -> dict | None:
    rows = await db.query_raw(
        f"select {_BROADCAST_COLUMNS} from notification_broadcasts"
        " where tenant_id = $1::uuid and sender_user_id = $2::uuid and request_key = $3",
        tenant_id,
        sender_user_id,
        request_key,
    )
    return rows[0] if rows else None


async def count_recent_broadcasts(db: Prisma, tenant_id: str, sender_user_id: str, *, within_seconds: int) -> int:
    rows = await db.query_raw(
        "select count(*)::int as n from notification_broadcasts"
        " where tenant_id = $1::uuid and sender_user_id = $2::uuid"
        " and created_at > now() - make_interval(secs => $3::double precision)",
        tenant_id,
        sender_user_id,
        float(within_seconds),
    )
    return rows[0]["n"]


async def insert_broadcast(
    db: Prisma,
    tenant_id: str,
    sender_user_id: str,
    request_key: str,
    *,
    audience: dict,
    audience_size: int,
    without_login: int,
    notified: int,
    opted_out: int,
    skipped: int,
) -> dict | None:
    """None when another request with this key got there first (the unique index)."""
    rows = await db.query_raw(
        f"""
        insert into notification_broadcasts
            (tenant_id, sender_user_id, request_key, audience, audience_size, without_login, notified,
             opted_out, skipped)
        values ($1::uuid, $2::uuid, $3, $4::jsonb, $5::int, $6::int, $7::int, $8::int, $9::int)
        on conflict (tenant_id, sender_user_id, request_key) do nothing
        returning {_BROADCAST_COLUMNS}
        """,
        tenant_id,
        sender_user_id,
        request_key,
        json.dumps(audience),
        audience_size,
        without_login,
        notified,
        opted_out,
        skipped,
    )
    return rows[0] if rows else None
