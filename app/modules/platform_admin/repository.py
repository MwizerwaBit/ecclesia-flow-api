from datetime import UTC, datetime, timedelta

from prisma import Prisma
from prisma.models import Organization

_LIST_SQL = """
    select o.id, o.display_name, o.slug, o.country, o.tier, o.status, o.created_at,
           o.trial_ends_at, o.renewal_date, o.verification_status, o.activation_source, o.activated_at,
           o.city, o.contact_email,
           (select count(*) from members m where m.tenant_id = o.id) as member_count,
           (select count(*) from org_documents d where d.tenant_id = o.id and d.status = 'pending_review')::int
             as documents_pending,
           (select s.status from billing_subscriptions s where s.tenant_id = o.id) as subscription_status,
           (select max(tm.last_active_at) from tenant_memberships tm where tm.tenant_id = o.id) as last_active_at
    from organizations o
"""


async def list_orgs(db: Prisma, *, search: str | None, status: str | None, tier: str | None) -> list[dict]:
    clauses = []
    params: list = []
    if search:
        params.append(f"%{search.lower()}%")
        n = len(params)
        params.append(search.lower())
        clauses.append(f"(lower(o.display_name) like ${n} or lower(o.slug) like ${n} or lower(o.country) = ${n + 1})")
    if status and status != "all":
        params.append(status)
        clauses.append(f"o.status = ${len(params)}")
    if tier and tier != "all":
        params.append(tier)
        clauses.append(f"o.tier = ${len(params)}")
    where_sql = (" where " + " and ".join(clauses)) if clauses else ""
    return await db.query_raw(_LIST_SQL + where_sql + " order by o.created_at desc", *params)


async def get_org_list_row(db: Prisma, org_id: str) -> dict | None:
    rows = await db.query_raw(_LIST_SQL + " where o.id = $1::uuid", org_id)
    return rows[0] if rows else None


_DETAIL_SQL = """
    select o.id, o.display_name, o.slug, o.country, o.tier, o.status, o.created_at,
           o.trial_ends_at, o.renewal_date,
           (select count(*) from members m where m.tenant_id = o.id) as member_count,
           (select max(tm.last_active_at) from tenant_memberships tm where tm.tenant_id = o.id) as last_active_at,
           leader.email, leader.first_name, leader.last_name
    from organizations o
    left join lateral (
      select u.email, u.first_name, u.last_name
      from tenant_memberships tm2 join users u on u.id = tm2.user_id
      where tm2.tenant_id = o.id and tm2.is_leader
      limit 1
    ) leader on true
"""


async def get_org_detail_row(db: Prisma, org_id: str) -> dict | None:
    rows = await db.query_raw(_DETAIL_SQL + " where o.id = $1::uuid", org_id)
    if not rows:
        return None
    row = rows[0]
    org = await db.organization.find_unique(where={"id": org_id})
    unit_count = await db.hierarchyunit.count(where={"tenant_id": org_id})
    return {
        **row,
        "legal_name": org.legal_name,
        "currency": org.currency,
        "timezone": org.timezone,
        "language": org.language,
        "logo_url": org.logo_url,
        "primary_color": org.primary_color,
        "custom_domain": org.custom_domain,
        "storage_used_mb": org.storage_used_mb,
        "unit_count": unit_count,
        "primary_admin_email": row.get("email"),
        "primary_admin_name": (f"{row['first_name']} {row['last_name']}" if row.get("first_name") else None),
    }


async def create_org(db: Prisma, data: dict) -> Organization:
    return await db.organization.create(data=data)


async def set_org_status(db: Prisma, org_id: str, status: str) -> None:
    await db.organization.update(where={"id": org_id}, data={"status": status})


async def list_feature_overrides(db: Prisma, tenant_id: str) -> list:
    return await db.featureflagoverride.find_many(where={"tenant_id": tenant_id})


async def upsert_feature_override(
    db: Prisma, *, tenant_id: str, code: str, is_enabled: bool, by_user_id: str, note: str | None, expires_at
) -> None:
    await db.featureflagoverride.upsert(
        where={"tenant_id_feature_code": {"tenant_id": tenant_id, "feature_code": code}},
        data={
            "create": {
                "tenant_id": tenant_id,
                "feature_code": code,
                "is_enabled": is_enabled,
                "overridden_by_user_id": by_user_id,
                "override_note": note,
                "override_expires_at": expires_at,
            },
            "update": {
                "is_enabled": is_enabled,
                "overridden_by_user_id": by_user_id,
                "override_note": note,
                "override_expires_at": expires_at,
                "overridden_at": datetime.now(UTC),
            },
        },
    )


async def list_audit_log(db: Prisma, *, org_id: str | None, action: str | None) -> list[dict]:
    clauses = []
    params: list = []
    if org_id:
        params.append(org_id)
        clauses.append(f"a.tenant_id = ${len(params)}::uuid")
    if action:
        params.append(f"%{action}%")
        clauses.append(f"a.action like ${len(params)}")
    where_sql = (" where " + " and ".join(clauses)) if clauses else ""
    return await db.query_raw(
        f"""
        select a.id, a.tenant_id as org_id, o.display_name as org_name, a.actor_user_id as user_id,
               (u.first_name || ' ' || u.last_name) as user_name,
               a.action, a.resource_type, a.resource_id, a.metadata, a.ip_address::text as ip_address,
               a.is_impersonated, a.impersonated_by_user_id as impersonated_by, a.created_at
        from audit_logs a
        left join organizations o on o.id = a.tenant_id
        left join users u on u.id = a.actor_user_id
        {where_sql}
        order by a.created_at desc
        limit 500
        """,
        *params,
    )


async def list_domains(db: Prisma) -> list[dict]:
    return await db.query_raw(
        """
        select d.*, o.display_name as org_name from custom_domains d join organizations o on o.id = d.tenant_id
        order by d.created_at desc
        """
    )


async def verify_domain(db: Prisma, domain_id: str) -> None:
    await db.customdomain.update(
        where={"id": domain_id},
        data={"status": "active", "ssl_provisioned": True, "verified_at": datetime.now(UTC)},
    )


async def get_domain(db: Prisma, domain_id: str):
    return await db.customdomain.find_unique(where={"id": domain_id})


async def list_erasure_requests(db: Prisma) -> list[dict]:
    return await db.query_raw(
        """
        select r.id, r.tenant_id as org_id, o.display_name as org_name, r.member_id,
               (m.first_name || ' ' || m.last_name) as member_display_name,
               (ru.first_name || ' ' || ru.last_name) as requested_by,
               r.requested_at, r.status, r.completed_at,
               (pu.first_name || ' ' || pu.last_name) as processed_by, r.notes
        from data_erasure_requests r
        join organizations o on o.id = r.tenant_id
        join members m on m.id = r.member_id
        join users ru on ru.id = r.requested_by_user_id
        left join users pu on pu.id = r.processed_by_user_id
        order by r.requested_at desc
        """
    )


async def get_erasure_request(db: Prisma, request_id: str):
    return await db.dataerasurerequest.find_unique(where={"id": request_id})


async def decide_erasure_request(
    db: Prisma, request_id: str, *, status: str, processed_by_user_id: str, notes: str | None
) -> None:
    data = {"status": status, "processed_by_user_id": processed_by_user_id}
    if notes is not None:
        data["notes"] = notes
    if status == "completed":
        data["completed_at"] = datetime.now(UTC)
    await db.dataerasurerequest.update(where={"id": request_id}, data=data)


async def anonymize_member(db: Prisma, member_id: str) -> None:
    await db.member.update(
        where={"id": member_id},
        data={
            "first_name": "Erased",
            "last_name": "Member",
            "email": None,
            "phone": None,
            "whatsapp": None,
            "photo_url": None,
            "address_line1": None,
            "address_line2": None,
        },
    )


async def list_platform_admins(db: Prisma) -> list[dict]:
    return await db.query_raw(
        """
        select u.id, (u.first_name || ' ' || u.last_name) as name, u.email, u.platform_admin_level as level,
               u.mfa_enabled, u.last_login_at,
               (select count(*) from impersonation_sessions s
                 where s.platform_admin_user_id = u.id) as impersonation_count
        from users u where u.is_platform_admin
        order by u.last_login_at desc nulls last
        """
    )


async def create_impersonation_session(
    db: Prisma, *, admin_user_id: str, tenant_id: str, role: str, expires_at: datetime
):
    return await db.impersonationsession.create(
        data={
            "platform_admin_user_id": admin_user_id,
            "tenant_id": tenant_id,
            "impersonated_role": role,
            "expires_at": expires_at,
        }
    )


async def platform_metrics_counts(db: Prisma) -> dict:
    total = await db.organization.count()
    active = await db.organization.count(where={"status": "active"})
    total_members = await db.member.count()
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)
    new_today = await db.organization.count(where={"created_at": {"gte": today_start}})
    new_week = await db.organization.count(where={"created_at": {"gte": week_start}})
    new_month = await db.organization.count(where={"created_at": {"gte": month_start}})
    trials_ending_rows = await db.query_raw(
        _LIST_SQL + " where o.status = 'trial' and o.trial_ends_at <= $1::timestamptz and o.trial_ends_at >= now()"
        " order by o.trial_ends_at asc",
        now + timedelta(days=7),
    )
    return {
        "total_orgs": total,
        "active_orgs": active,
        "total_members": total_members,
        "new_signups_today": new_today,
        "new_signups_this_week": new_week,
        "new_signups_this_month": new_month,
        "trials_ending_in_7_days": trials_ending_rows,
    }
