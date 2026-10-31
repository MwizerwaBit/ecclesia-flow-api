import secrets
from datetime import UTC, datetime

from prisma import Json, Prisma

_FORM_COLUMNS = (
    "f.id, f.title, f.description, f.status, f.unit_id, f.owner_kind, f.owner_id, f.public_key, "
    "f.created_by_user_id, f.created_at, f.updated_at, f.published_at, f.closed_at, "
    "(select count(*) from form_submissions s where s.form_id = f.id)::int as submission_count"
)
_SUBMISSION_COLUMNS = "id, respondent_name, respondent_email, respondent_phone, answers, created_at"


def new_public_key() -> str:
    return secrets.token_urlsafe(18)  # 24 URL-safe characters from the OS random source


async def church_timezone(db: Prisma) -> str | None:
    rows = await db.query_raw("select timezone from organizations where id = current_tenant_id()")
    return rows[0]["timezone"] if rows else None


# ── Forms ────────────────────────────────────────────────────────────────


async def get_form(db: Prisma, form_id: str) -> dict | None:
    rows = await db.query_raw(f"select {_FORM_COLUMNS} from forms f where f.id = $1::uuid", form_id)
    return rows[0] if rows else None


async def get_form_by_public_key(db: Prisma, public_key: str) -> dict | None:
    rows = await db.query_raw(f"select {_FORM_COLUMNS} from forms f where f.public_key = $1", public_key)
    return rows[0] if rows else None


async def get_owned_form(db: Prisma, owner_kind: str, owner_id: str) -> dict | None:
    rows = await db.query_raw(
        f"select {_FORM_COLUMNS} from forms f where f.owner_kind = $1 and f.owner_id = $2::uuid", owner_kind, owner_id
    )
    return rows[0] if rows else None


async def list_standalone(db: Prisma, status: str | None) -> list[dict]:
    """Standalone forms, newest first. Event forms are not listed: they belong to
    their event, and whoever may see the event reaches them there."""
    params: list = []
    where = "f.owner_kind is null"
    if status:
        params.append(status)
        where += f" and f.status = ${len(params)}"
    return await db.query_raw(
        f"select {_FORM_COLUMNS} from forms f where {where} order by f.created_at desc, f.id desc", *params
    )


async def create_form(
    db: Prisma,
    tenant_id: str,
    *,
    title: str,
    description: str | None,
    unit_id: str | None,
    created_by_user_id: str | None,
    status: str = "draft",
    owner: tuple[str, str] | None = None,
) -> str:
    row = await db.form.create(
        data={
            "tenant_id": tenant_id,
            "unit_id": unit_id,
            "owner_kind": owner[0] if owner else None,
            "owner_id": owner[1] if owner else None,
            "title": title,
            "description": description,
            "status": status,
            "public_key": new_public_key(),
            "created_by_user_id": created_by_user_id,
        }
    )
    return row.id


async def get_or_create_owned(
    db: Prisma,
    tenant_id: str,
    owner: tuple[str, str],
    *,
    title: str,
    unit_id: str | None,
    created_by_user_id: str | None,
) -> dict:
    """The owner's form, made (published, so it takes answers) if it has none yet.
    ON CONFLICT rather than catch-and-retry: a failed insert would abort the whole
    request transaction, and two people can open the same event's form at once."""
    await db.execute_raw(
        """
        insert into forms (tenant_id, unit_id, owner_kind, owner_id, title, status, public_key,
                           created_by_user_id, published_at)
        values ($1::uuid, $2::uuid, $3, $4::uuid, $5, 'published', $6, $7::uuid, now())
        on conflict (tenant_id, owner_kind, owner_id) where owner_kind is not null do nothing
        """,
        tenant_id,
        unit_id,
        owner[0],
        owner[1],
        title,
        new_public_key(),
        created_by_user_id,
    )
    form = await get_owned_form(db, owner[0], owner[1])
    assert form is not None
    return form


async def update_form(db: Prisma, form_id: str, data: dict) -> None:
    await db.form.update(where={"id": form_id}, data={**data, "updated_at": datetime.now(UTC)})


async def set_status(db: Prisma, form_id: str, status: str) -> None:
    await db.execute_raw(
        """
        update forms set status = $2, updated_at = now(),
               published_at = case when $2 = 'published' then coalesce(published_at, now()) else published_at end,
               closed_at = case when $2 = 'closed' then now() when $2 = 'published' then null else closed_at end
         where id = $1::uuid
        """,
        form_id,
        status,
    )


async def touch(db: Prisma, form_id: str) -> None:
    await db.execute_raw("update forms set updated_at = now() where id = $1::uuid", form_id)


async def delete_form(db: Prisma, form_id: str) -> None:
    await db.form.delete(where={"id": form_id})


# ── Questions ────────────────────────────────────────────────────────────


async def list_fields(db: Prisma, form_id: str) -> list[dict]:
    rows = await db.formfield.find_many(where={"form_id": form_id}, order=[{"position": "asc"}, {"id": "asc"}])
    return [r.model_dump() for r in rows]


async def replace_fields(db: Prisma, tenant_id: str, form_id: str, fields: list[dict]) -> list[dict]:
    """Full replace: the caller always sends the whole form in display order.
    Safe because a submission keeps its own copy of the questions it answered.

    Runs on the request's own transaction rather than opening a nested one: a
    second ``db.tx()`` put the inserts outside the scope where the RLS session
    variable is visible, tripping ``tenant_isolation``'s WITH CHECK."""
    await db.formfield.delete_many(where={"form_id": form_id})
    created = []
    for position, field in enumerate(fields):
        row = await db.formfield.create(
            data={**field, "tenant_id": tenant_id, "form_id": form_id, "position": position}
        )
        created.append(row.model_dump())
    return created


# ── Submissions ──────────────────────────────────────────────────────────


async def create_submission(db: Prisma, tenant_id: str, form_id: str, data: dict, snapshot: dict) -> dict:
    row = await db.formsubmission.create(
        data={
            "tenant_id": tenant_id,
            "form_id": form_id,
            "respondent_name": data["respondent_name"],
            "respondent_email": data.get("respondent_email"),
            "respondent_phone": data.get("respondent_phone"),
            "answers": Json(data["answers"]),
            "form_snapshot": Json(snapshot),
        }
    )
    return row.model_dump()


async def list_submissions(db: Prisma, form_id: str, *, limit: int | None = None) -> list[dict]:
    sql = (
        f"select {_SUBMISSION_COLUMNS} from form_submissions where form_id = $1::uuid order by created_at desc, id desc"
    )
    if limit is not None:
        return await db.query_raw(sql + " limit $2", form_id, limit)
    return await db.query_raw(sql, form_id)


async def get_submission(db: Prisma, form_id: str, submission_id: str) -> dict | None:
    rows = await db.query_raw(
        f"select {_SUBMISSION_COLUMNS}, form_snapshot from form_submissions where form_id = $1::uuid and id = $2::uuid",
        form_id,
        submission_id,
    )
    return rows[0] if rows else None


async def submissions_with_snapshots(db: Prisma, form_id: str, limit: int) -> list[dict]:
    return await db.query_raw(
        f"select {_SUBMISSION_COLUMNS}, form_snapshot from form_submissions where form_id = $1::uuid "
        "order by created_at desc, id desc limit $2",
        form_id,
        limit,
    )


async def respondent_emails(db: Prisma, form_id: str) -> list[str]:
    rows = await db.query_raw(
        "select distinct lower(respondent_email) as email from form_submissions "
        "where form_id = $1::uuid and respondent_email is not null",
        form_id,
    )
    return [r["email"] for r in rows]
