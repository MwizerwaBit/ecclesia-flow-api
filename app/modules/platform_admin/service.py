from datetime import UTC, datetime, timedelta

from prisma import Prisma

from app.core.exceptions import ConflictError, NotFoundError
from app.modules.audit.service import write_audit
from app.modules.platform_admin import repository
from app.modules.platform_admin.models import FEATURE_CATALOGUE, tier_default_enabled
from app.modules.platform_admin.schemas import (
    CreateOrgInput,
    DecideErasureRequestInput,
    ImpersonationRequest,
    SetFeatureFlagInput,
    SetOrgStatusInput,
)
from app.modules.tenant.service import create_organization_with_root_unit

CRITICAL_ACTIONS = {
    "org.suspended",
    "impersonation.started",
    "member.exported",
    "member.bulk_deleted",
    "donation.voided",
    "role.permissions_changed",
    "data.erasure_completed",
}


async def list_orgs(db: Prisma, *, search: str | None, status: str | None, tier: str | None) -> list[dict]:
    return await repository.list_orgs(db, search=search, status=status, tier=tier)


async def get_org(db: Prisma, org_id: str) -> dict:
    row = await repository.get_org_list_row(db, org_id)
    if row is None:
        raise NotFoundError("No such organization")
    return row


async def get_org_detail(db: Prisma, org_id: str) -> dict:
    row = await repository.get_org_detail_row(db, org_id)
    if row is None:
        raise NotFoundError("No such organization")
    return row


async def create_org(db: Prisma, admin_user_id: str, payload: CreateOrgInput) -> dict:

    org, _unit_id = await create_organization_with_root_unit(
        db,
        legal_name=payload.legal_name,
        display_name=payload.display_name,
        country=payload.country,
        currency=payload.currency,
        timezone_name=payload.timezone,
    )
    # Registered by EcclesiaFlow staff: active from the start, on the chosen
    # plan, with the reason recorded. The church's leader gets a real
    # invitation (set-password link) rather than a password-less account.
    from app.modules.rbac.models import SYSTEM_ROLE_LEADER_ID
    from app.modules.team import service as team_service

    await db.organization.update(
        where={"id": org.id},
        data={
            "status": "active",
            "tier": payload.tier,
            "activated_at": datetime.now(UTC),
            "activation_source": "platform_admin",
            "activation_note": "Registered by EcclesiaFlow",
            "trial_ends_at": datetime.now(UTC) + timedelta(days=payload.trial_days),
        },
    )
    name_parts = payload.primary_admin_name.split(" ", 1)
    invited = await team_service.invite_with_role(
        db,
        tenant_id=org.id,
        email=payload.primary_admin_email,
        first_name=name_parts[0],
        last_name=name_parts[1] if len(name_parts) > 1 else "",
        role_id=SYSTEM_ROLE_LEADER_ID,
        unit_scope_id=None,
        is_leader=True,
    )

    await write_audit(
        db,
        tenant_id=org.id,
        actor_user_id=admin_user_id,
        action="org.registered_by_platform",
        resource_type="organization",
        resource_id=org.id,
    )
    await write_audit(
        db,
        tenant_id=None,
        actor_user_id=admin_user_id,
        action="org.registered_by_platform",
        resource_type="organization",
        resource_id=org.id,
    )
    return {**await get_org(db, org.id), "invite_url": invited.get("invite_url")}


async def set_org_status(db: Prisma, admin_user_id: str, org_id: str, payload: SetOrgStatusInput) -> dict:
    from app.modules.org import lifecycle

    await get_org(db, org_id)  # 404 if it doesn't exist
    await lifecycle.transition(
        db, org_id, payload.status, actor_user_id=admin_user_id, source="platform_admin", note=payload.reason
    )
    action = "org.suspended" if payload.status == "suspended" else "org.status_changed"
    await write_audit(
        db,
        tenant_id=org_id,
        actor_user_id=admin_user_id,
        action=action,
        resource_type="organization",
        resource_id=org_id,
        metadata={"status": payload.status, "reason": payload.reason},
    )
    await write_audit(
        db,
        tenant_id=None,
        actor_user_id=admin_user_id,
        action=action,
        resource_type="organization",
        resource_id=org_id,
        metadata={"status": payload.status, "reason": payload.reason},
    )
    return await get_org(db, org_id)


async def list_feature_flags(db: Prisma, org_id: str) -> list[dict]:
    org = await get_org(db, org_id)
    overrides = {o.feature_code: o for o in await repository.list_feature_overrides(db, org_id)}
    result = []
    for item in FEATURE_CATALOGUE:
        default = tier_default_enabled(item["min_tier"], org["tier"])
        override = overrides.get(item["code"])
        current = override.is_enabled if override else default
        result.append(
            {
                "code": item["code"],
                "label": item["label"],
                "description": item["description"],
                "tier_default": default,
                "current_value": current,
                "is_overridden": current != default,
                "override_set_by": override.overridden_by_user_id if override else None,
                "override_set_at": override.overridden_at if override else None,
                "override_expires_at": override.override_expires_at if override else None,
                "override_note": override.override_note if override else None,
            }
        )
    return result


async def set_feature_flag(
    db: Prisma, admin_user_id: str, org_id: str, code: str, payload: SetFeatureFlagInput
) -> dict:
    if code not in {f["code"] for f in FEATURE_CATALOGUE}:
        raise NotFoundError("No such feature flag")
    await repository.upsert_feature_override(
        db,
        tenant_id=org_id,
        code=code,
        is_enabled=payload.value,
        by_user_id=admin_user_id,
        note=payload.note,
        expires_at=payload.expires_at,
    )
    await write_audit(
        db,
        tenant_id=org_id,
        actor_user_id=admin_user_id,
        action="feature_flag.overridden",
        resource_type="feature_flag",
        resource_id=None,
        metadata={"code": code, "value": payload.value, "note": payload.note},
    )
    flags = await list_feature_flags(db, org_id)
    return next(f for f in flags if f["code"] == code)


async def list_audit_log(db: Prisma, *, org_id: str | None, action: str | None) -> list[dict]:
    return await repository.list_audit_log(db, org_id=org_id, action=action)


async def list_domains(db: Prisma) -> list[dict]:
    return await repository.list_domains(db)


async def verify_domain(db: Prisma, domain_id: str) -> dict:
    domain = await repository.get_domain(db, domain_id)
    if domain is None:
        raise NotFoundError("No such domain")
    await repository.verify_domain(db, domain_id)
    domains = await repository.list_domains(db)
    return next(d for d in domains if d["id"] == domain_id)


async def list_erasure_requests(db: Prisma) -> list[dict]:
    return await repository.list_erasure_requests(db)


async def decide_erasure_request(
    db: Prisma, admin_user_id: str, request_id: str, payload: DecideErasureRequestInput
) -> dict:
    request = await repository.get_erasure_request(db, request_id)
    if request is None:
        raise NotFoundError("No such erasure request")
    if request.status not in ("pending",):
        raise ConflictError("This request has already been decided")

    if payload.decision == "approved":
        await repository.anonymize_member(db, request.member_id)
        status = "completed"
        await write_audit(
            db,
            tenant_id=request.tenant_id,
            actor_user_id=admin_user_id,
            action="data.erasure_completed",
            resource_type="member",
            resource_id=request.member_id,
        )
        await write_audit(
            db,
            tenant_id=None,
            actor_user_id=admin_user_id,
            action="data.erasure_completed",
            resource_type="member",
            resource_id=request.member_id,
        )
    else:
        status = "rejected"
    await repository.decide_erasure_request(
        db, request_id, status=status, processed_by_user_id=admin_user_id, notes=payload.notes
    )
    rows = await repository.list_erasure_requests(db)
    return next(r for r in rows if r["id"] == request_id)


async def list_platform_admins(db: Prisma) -> list[dict]:
    return await repository.list_platform_admins(db)


async def get_platform_metrics(db: Prisma) -> dict:
    counts = await repository.platform_metrics_counts(db)
    return {
        **counts,
        # MRR needs a real billing-provider integration (Stripe/Flutterwave)
        # that isn't connected — 0.0 here is an honest "not wired up yet",
        # not a fabricated number. Same for the three API latency
        # percentiles and queue depth, which need real APM, not a database
        # query — None, not a made-up value.
        "mrr": 0.0,
        "api_p50_ms": None,
        "api_p95_ms": None,
        "api_p99_ms": None,
        "error_rate": None,
        "queue_depth": None,
    }


async def get_system_health(db: Prisma) -> dict:
    active_sessions = await db.refreshtoken.count(where={"revoked_at": None, "expires_at": {"gt": datetime.now(UTC)}})
    return {
        "services": [
            {"name": "API", "status": "operational", "detail": "Responding normally"},
            {"name": "Database", "status": "operational", "detail": "Connected"},
        ],
        "active_sessions": active_sessions,
        # Failed background jobs / rate-limit violations / error aggregation
        "failed_jobs": None,
        "rate_limit_violations": [],
        "recent_errors": [],
    }


# ───────────────────────────── Impersonation ─────────────────────────────

IMPERSONATION_RESTRICTED_PERMISSIONS = {
    "pastoral_notes:read",
    "pastoral_notes:write",
    "finance:export",
    "members:export",
}


async def start_impersonation(db: Prisma, admin_user_id: str, org_id: str, payload: ImpersonationRequest) -> dict:
    from app.core.security import create_access_token
    from app.modules.rbac import repository as rbac_repository
    from app.modules.rbac.service import resolve_permissions

    org = await get_org(db, org_id)
    role = await rbac_repository.get_system_role_by_name(db, payload.role_to_impersonate)
    if role is None:
        raise NotFoundError(f"No such role: {payload.role_to_impersonate}")
    permissions = await resolve_permissions(db, role.id)
    # Impersonation is support/debugging, never a back door to the most
    # sensitive data categories — screen-inventory.md PA-08 is explicit that
    # it "cannot be used to read pastoral notes [or] download financial
    # exports", so those permissions are stripped regardless of what the
    # impersonated role would normally carry.
    safe_permissions = [p for p in permissions if p not in IMPERSONATION_RESTRICTED_PERMISSIONS]

    expires_at = datetime.now(UTC) + timedelta(hours=1)
    session = await repository.create_impersonation_session(
        db, admin_user_id=admin_user_id, tenant_id=org_id, role=payload.role_to_impersonate, expires_at=expires_at
    )
    token = create_access_token(
        user_id=admin_user_id,
        membership_id=None,
        tenant_id=org_id,
        role_id=role.id,
        permissions=safe_permissions,
        unit_scope_id=None,
        # Scoped to the impersonated tenant only, not platform-wide, for the session's duration.
        is_platform_admin=False,
        platform_admin_level=None,
        mfa_verified=True,
    )
    await write_audit(
        db,
        tenant_id=org_id,
        actor_user_id=admin_user_id,
        action="impersonation.started",
        resource_type="organization",
        resource_id=org_id,
        is_impersonated=True,
        impersonated_by_user_id=admin_user_id,
    )
    await write_audit(
        db,
        tenant_id=None,
        actor_user_id=admin_user_id,
        action="impersonation.started",
        resource_type="organization",
        resource_id=org_id,
        is_impersonated=True,
        impersonated_by_user_id=admin_user_id,
    )
    return {
        "org_id": org_id,
        "org_name": org["display_name"],
        "role": payload.role_to_impersonate,
        "token": token,
        "expires_at": int(session.expires_at.timestamp() * 1000),
    }


# ───────────────────────────── Registration review ─────────────────────────────


async def activate_org(db: Prisma, admin_user_id: str, org_id: str, note: str) -> dict:
    """Manual activation, e.g. a church paying by bank transfer or a sponsored one."""
    from app.modules.org import lifecycle

    await lifecycle.transition(db, org_id, "active", actor_user_id=admin_user_id, source="platform_admin", note=note)
    await write_audit(
        db,
        tenant_id=None,
        actor_user_id=admin_user_id,
        action="org.activated_by_platform",
        resource_type="organization",
        resource_id=org_id,
        metadata={"note": note},
    )
    return await get_org(db, org_id)


async def list_org_documents(db: Prisma, org_id: str) -> list[dict]:
    from app.modules.org import repository as org_repository

    await get_org(db, org_id)
    return await org_repository.list_documents(db, org_id)


async def read_org_document(db: Prisma, org_id: str, document_id: str):
    from app.modules.org import service as org_service

    return await org_service.read_document(db, org_id, document_id)


async def review_org_document(
    db: Prisma, admin_user_id: str, org_id: str, document_id: str, decision: str, note: str | None
):
    from datetime import UTC
    from datetime import datetime as dt

    from app.modules.org import repository as org_repository

    doc = await org_repository.get_document(db, org_id, document_id)
    if doc is None:
        raise NotFoundError("No such document")
    if doc.status != "pending_review":
        raise ConflictError("This document has already been reviewed or replaced")
    await db.orgdocument.update(
        where={"id": document_id},
        data={
            "status": decision,
            "reviewed_by_user_id": admin_user_id,
            "reviewed_at": dt.now(UTC),
            "review_note": note,
        },
    )
    await db.organization.update(where={"id": org_id}, data={"verification_status": decision})
    await write_audit(
        db,
        tenant_id=org_id,
        actor_user_id=admin_user_id,
        action=f"org.certificate_{decision}",
        resource_type="org_document",
        resource_id=document_id,
        metadata={"note": note},
    )
    return await org_repository.list_documents(db, org_id)


async def org_billing(db: Prisma, org_id: str) -> dict:
    from app.modules.billing import service as billing_service

    await get_org(db, org_id)
    return await billing_service.overview(db, org_id)
