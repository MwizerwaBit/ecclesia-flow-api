from prisma import Json, Prisma
from prisma.models import Certificate, CertificateTemplate


async def list_templates(db: Prisma, *, category: str | None) -> list[CertificateTemplate]:
    where = {"category": category} if category else {}
    return await db.certificatetemplate.find_many(where=where, order={"created_at": "desc"})


async def get_template(db: Prisma, template_id: str) -> CertificateTemplate | None:
    return await db.certificatetemplate.find_unique(where={"id": template_id})


async def create_template(db: Prisma, tenant_id: str, data: dict) -> CertificateTemplate:
    data = dict(data)
    data["tokens"] = Json(data.get("tokens") or [])
    if data.get("qr_code_position") is not None:
        data["qr_code_position"] = Json(data["qr_code_position"])
    else:
        data.pop("qr_code_position", None)
    return await db.certificatetemplate.create(data={**data, "tenant_id": tenant_id})


async def update_template(db: Prisma, template_id: str, data: dict) -> CertificateTemplate:
    data = dict(data)
    if "tokens" in data:
        data["tokens"] = Json(data["tokens"])
    if "qr_code_position" in data:
        if data["qr_code_position"] is not None:
            data["qr_code_position"] = Json(data["qr_code_position"])
        else:
            data.pop("qr_code_position")
    return await db.certificatetemplate.update(where={"id": template_id}, data=data)


async def list_issued(db: Prisma, *, member_id: str | None) -> list[dict]:
    where_sql = "where c.member_id = $1::uuid" if member_id else ""
    params = [member_id] if member_id else []
    return await db.query_raw(
        f"""
        select c.*, t.name as template_name, (m.first_name || ' ' || m.last_name) as member_name,
               (u.first_name || ' ' || u.last_name) as issued_by_name
        from certificates c
        join certificate_templates t on t.id = c.template_id
        join members m on m.id = c.member_id
        join users u on u.id = c.issued_by_user_id
        {where_sql}
        order by c.issued_at desc
        """,
        *params,
    )


async def get_issued_row(db: Prisma, certificate_id: str) -> dict | None:
    rows = await db.query_raw(
        """
        select c.*, t.name as template_name, (m.first_name || ' ' || m.last_name) as member_name,
               (u.first_name || ' ' || u.last_name) as issued_by_name
        from certificates c
        join certificate_templates t on t.id = c.template_id
        join members m on m.id = c.member_id
        join users u on u.id = c.issued_by_user_id
        where c.id = $1::uuid
        """,
        certificate_id,
    )
    return rows[0] if rows else None


async def next_serial_number(db: Prisma, tenant_id: str, category: str, prefix: str) -> str:
    """Advisory-locked so two concurrent issuances for the same
    tenant+category can never produce the same serial — the lock is held
    for the rest of this transaction (pg_advisory_xact_lock), released
    automatically on commit/rollback."""
    await db.execute_raw("select pg_advisory_xact_lock(hashtextextended($1, 0))", f"{tenant_id}:{category}")
    rows = await db.query_raw(
        """
        select count(*) as n from certificates c join certificate_templates t on t.id = c.template_id
        where c.tenant_id = $1::uuid and t.category = $2
        """,
        tenant_id,
        category,
    )
    from datetime import UTC, datetime

    seq = int(rows[0]["n"]) + 1
    year = datetime.now(UTC).year
    return f"{prefix}-{year}-{seq:04d}"


async def create_certificate(db: Prisma, data: dict) -> Certificate:
    data = dict(data)
    data["custom_values"] = Json(data.get("custom_values") or {})
    return await db.certificate.create(data=data)


async def revoke_certificate(db: Prisma, certificate_id: str, *, reason: str) -> None:
    from datetime import UTC, datetime

    await db.certificate.update(
        where={"id": certificate_id},
        data={"is_revoked": True, "revoked_reason": reason, "revoked_at": datetime.now(UTC)},
    )


async def verify_by_hash(db: Prisma, qr_hash: str) -> dict | None:
    # Plain `select ... from certificates where qr_hash = $1` would return
    # zero rows here regardless of the hash — this call runs with no tenant
    # context (public, unauthenticated), and RLS's `tenant_id =
    # current_tenant_id()` never matches a NULL setting. The SECURITY
    # DEFINER function (migration 20261002104249) is what makes this one
    # narrow, secret-keyed read possible without bypassing RLS generally.
    rows = await db.query_raw("select * from verify_certificate_by_hash($1)", qr_hash)
    return rows[0] if rows else None
