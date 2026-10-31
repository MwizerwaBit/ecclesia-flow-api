from prisma import Prisma


async def export_members(db: Prisma) -> list[dict]:
    """Every person the church's row-level policy lets this session see, with
    the names (not ids) a spreadsheet needs. The ID number is never selected:
    only a hash, ciphertext and last four are stored (app/core/pii.py)."""
    return await db.query_raw(
        """
        select m.id::text, m.first_name, m.last_name, m.gender, m.status, m.phone, m.email, m.id_type,
               m.date_of_birth, m.title, m.middle_name, m.preferred_name, m.whatsapp, m.marital_status,
               m.occupation, m.employer, m.address_line1, m.city, m.state, m.country, m.postal_code,
               m.joined_at, m.join_method, m.is_baptised, m.baptism_date, m.envelope_number,
               m.household_role, m.emergency_contact_name, m.emergency_contact_phone,
               m.emergency_contact_relationship, m.consent_communications, m.notes,
               m.unit_id::text as unit_id,
               h.name as household_name,
               array(select g.name from group_memberships gm join groups g on g.id = gm.group_id
                      where gm.member_id = m.id and gm.status = 'active' and not g.is_archived
                      order by g.name) as group_names,
               array(select um.unit_id::text from unit_memberships um
                      where um.member_id = m.id and um.kind = 'associate' and um.status = 'active')
                 as associate_unit_ids
        from members m
        left join households h on h.id = m.household_id
        order by m.last_name, m.first_name
        """
    )


async def group_ids_by_name(db: Prisma) -> dict[str, str]:
    rows = await db.query_raw("select id::text, name from groups where not is_archived")
    return {r["name"].strip().lower(): r["id"] for r in rows}


async def household_ids_by_name(db: Prisma) -> dict[str, str]:
    rows = await db.query_raw("select id::text, name from households order by created_at")
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(r["name"].strip().lower(), r["id"])  # the oldest one wins a name clash
    return out


async def envelope_taken(db: Prisma, envelope: str) -> bool:
    rows = await db.query_raw("select tenant_envelope_taken($1, null::uuid) as taken", envelope)
    return bool(rows[0]["taken"])


async def member_with_id_hash(db: Prisma, id_hash: str) -> dict | None:
    rows = await db.query_raw("select first_name, last_name from members where national_id_hash = $1 limit 1", id_hash)
    return rows[0] if rows else None
