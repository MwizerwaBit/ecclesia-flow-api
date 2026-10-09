"""The church's own view of itself: profile, plan modules, official documents
and the onboarding checklist that leads from registration to activation."""

from prisma import Prisma

from app.core import private_storage
from app.core.authz import Scope
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.audit.service import write_audit
from app.modules.org import repository
from app.modules.org.catalogue import MODULE_BY_ID, tier_at_least
from app.modules.org.schemas import InvitePersonInput, OrgProfileUpdate
from app.modules.rbac.models import SYSTEM_ROLE_BOARD_ID, SYSTEM_ROLE_LEADER_ID

ACTIVE_STATUSES = {"active", "trial"}


async def _org_or_404(db: Prisma, org_id: str):
    org = await repository.get_org(db, org_id)
    if org is None:
        raise NotFoundError("No such organisation")
    return org


# ───────────────────────────── Profile ─────────────────────────────


async def get_profile(db: Prisma, org_id: str):
    return await _org_or_404(db, org_id)


async def update_profile(db: Prisma, org_id: str, actor_user_id: str, payload: OrgProfileUpdate):
    await _org_or_404(db, org_id)
    data = payload.model_dump(exclude_unset=True)
    if "website" in data and data["website"] is not None:
        data["website"] = str(data["website"])
    if "contact_email" in data and data["contact_email"]:
        data["contact_email"] = data["contact_email"].lower()
    org = await repository.update_org(db, org_id, data)
    await write_audit(
        db,
        tenant_id=org_id,
        actor_user_id=actor_user_id,
        action="org.profile_updated",
        resource_type="organization",
        resource_id=org_id,
        metadata={"fields": sorted(data)},
    )
    return org


# ───────────────────────────── Modules ─────────────────────────────


async def get_subscription(db: Prisma, org_id: str) -> dict:
    org = await _org_or_404(db, org_id)
    overrides = await repository.list_module_overrides(db, org_id)
    return {
        "tier": org.tier,
        "overrides": {o.module_id: o.enabled for o in overrides},
        "platform_managed": sorted(o.module_id for o in overrides if o.set_by_platform),
    }


async def set_module(
    db: Prisma, org_id: str, module_id: str, enabled: bool | None, *, actor_user_id: str, by_platform: bool
) -> dict:
    """Church admins can switch modules within their plan; only the platform
    can grant a module above the plan or override a platform decision."""
    module = MODULE_BY_ID.get(module_id)
    if module is None:
        raise NotFoundError("No such module")
    if module.core:
        raise ConflictError(f"{module.name} is part of every plan and can't be switched off")

    org = await _org_or_404(db, org_id)
    current = {o.module_id: o for o in await repository.list_module_overrides(db, org_id)}
    if not by_platform:
        if module_id in current and current[module_id].set_by_platform:
            raise ForbiddenError(
                f"{module.name} is managed by EcclesiaFlow support for your church", code="platform_managed"
            )
        if enabled is True and not tier_at_least(org.tier, module.min_tier):
            raise ForbiddenError(
                f"{module.name} is included from the {module.min_tier.title()} plan — upgrade to turn it on",
                code="upgrade_required",
            )

    if enabled is None:
        await repository.delete_module_override(db, org_id, module_id)
    else:
        await repository.upsert_module_override(
            db, tenant_id=org_id, module_id=module_id, enabled=enabled, user_id=actor_user_id, by_platform=by_platform
        )
    await write_audit(
        db,
        tenant_id=org_id,
        actor_user_id=actor_user_id,
        action="org.module_set",
        resource_type="organization",
        resource_id=org_id,
        metadata={"module": module_id, "enabled": enabled, "by_platform": by_platform},
    )
    return await get_subscription(db, org_id)


# ───────────────────────────── Official documents ─────────────────────────────


async def upload_certificate(db: Prisma, org_id: str, actor_user_id: str, file_name: str, data: bytes) -> dict:
    """Stores a government registration certificate privately and puts the
    church's verification into review. A newer upload supersedes any still
    waiting for review."""
    stored = private_storage.save(org_id, data)
    await repository.supersede_documents(db, org_id, "government_certificate")
    doc = await repository.create_document(
        db,
        {
            "tenant_id": org_id,
            "kind": "government_certificate",
            # Display only, never used as a path.
            "file_name": (file_name or "certificate")[:200],
            "mime_type": stored.mime_type,
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
            "storage_key": stored.storage_key,
            "uploaded_by_user_id": actor_user_id,
        },
    )
    await repository.update_org(db, org_id, {"verification_status": "pending_review"})
    await write_audit(
        db,
        tenant_id=org_id,
        actor_user_id=actor_user_id,
        action="org.certificate_uploaded",
        resource_type="org_document",
        resource_id=doc.id,
        metadata={"sha256": stored.sha256},
    )
    return (await repository.list_documents(db, org_id))[0]


async def list_documents(db: Prisma, org_id: str) -> list[dict]:
    return await repository.list_documents(db, org_id)


async def read_document(db: Prisma, org_id: str, document_id: str) -> tuple[bytes, str, str]:
    doc = await repository.get_document(db, org_id, document_id)
    if doc is None:
        raise NotFoundError("No such document")
    return private_storage.read(doc.storage_key, doc.sha256), doc.mime_type, doc.file_name


# ───────────────────────────── People in charge ─────────────────────────────


async def invite_person_in_charge(db: Prisma, org_id: str, scope: Scope, payload: InvitePersonInput) -> dict:
    """Invite the church leader (only while the church has none) or an
    administrator (appointing administrators is the leader's power)."""
    from app.modules.team import service as team_service

    people = await repository.people_in_charge(db, org_id)
    if payload.role == "leader":
        if any(p["is_leader"] for p in people):
            raise ConflictError("This church already has a leader — use a leadership transfer to change it")
        role_id, is_leader = SYSTEM_ROLE_LEADER_ID, True
    else:
        if not scope.has("admins:manage"):
            raise ForbiddenError("Only the church leader can appoint administrators", code="leader_only")
        role_id, is_leader = SYSTEM_ROLE_BOARD_ID, False

    return await team_service.invite_with_role(
        db,
        tenant_id=org_id,
        email=payload.email,
        first_name=payload.first_name,
        last_name=payload.last_name,
        role_id=role_id,
        unit_scope_id=None,
        is_leader=is_leader,
    )


# ───────────────────────────── Onboarding ─────────────────────────────


async def onboarding_status(db: Prisma, org_id: str) -> dict:
    org = await _org_or_404(db, org_id)
    people = await repository.people_in_charge(db, org_id)
    leader = next((p for p in people if p["is_leader"]), None)
    admins = [p for p in people if str(p["role_id"]) == SYSTEM_ROLE_BOARD_ID]
    branches = await repository.count_branches(db)
    certificate = await repository.latest_document(db, org_id, "government_certificate")
    subscription = await repository.get_subscription(db, org_id)

    def person(p) -> str:
        return f"{p['first_name']} {p['last_name']}".strip() or p["email"]

    profile_done = bool(org.legal_name and org.contact_phone and org.city)
    steps = [
        {
            "key": "church_profile",
            "title": "Church details",
            "required": True,
            "state": "done" if profile_done else "todo",
            "detail": "Legal name, contact phone and town are on file."
            if profile_done
            else "Add a contact phone and town so members and reviewers can reach you.",
        },
        {
            "key": "leader",
            "title": "Church leader",
            "required": True,
            "state": "todo" if leader is None else ("done" if leader["status"] == "active" else "pending"),
            "detail": "Invite the person who leads the church."
            if leader is None
            else f"{person(leader)}"
            + (" has accepted." if leader["status"] == "active" else " has been invited and hasn't accepted yet."),
        },
        {
            "key": "administrator",
            "title": "Administrator",
            "required": False,
            "state": "done" if any(a["status"] == "active" for a in admins) else ("pending" if admins else "todo"),
            "detail": "Someone to run day-to-day administration. The leader can do this too."
            if not admins
            else ", ".join(person(a) + ("" if a["status"] == "active" else " (invited)") for a in admins),
        },
        {
            "key": "branches",
            "title": "Branches",
            "required": False,
            "state": "done" if branches else "todo",
            "detail": f"{branches} branch{'es' if branches != 1 else ''} set up."
            if branches
            else "Optional — add branches or zones if your church meets in more than one place.",
        },
        {
            "key": "certificate",
            "title": "Government registration certificate",
            "required": False,
            "state": {
                None: "todo",
                "pending_review": "pending",
                "verified": "done",
                "rejected": "todo",
                "superseded": "pending",
            }[certificate.status if certificate else None],
            "detail": "Recommended — verified churches get a badge on their public page."
            if certificate is None
            else {
                "pending_review": "Uploaded — waiting for review.",
                "verified": "Verified.",
                "rejected": f"Not accepted: {certificate.review_note or 'please upload a clearer copy'}.",
                "superseded": "Uploaded — waiting for review.",
            }[certificate.status],
        },
        {
            "key": "payment",
            "title": "Plan & payment",
            "required": True,
            "state": "done"
            if subscription and subscription.status == "active"
            else ("done" if org.status in ACTIVE_STATUSES else "todo"),
            "detail": f"{org.tier.title()} plan active."
            if (subscription and subscription.status == "active")
            else (
                "Activated by EcclesiaFlow."
                if org.activation_source == "platform_admin"
                else "Choose a plan and pay to activate your church automatically."
            ),
        },
    ]
    is_active = org.status in ACTIVE_STATUSES
    next_action = None
    if org.status == "pending":
        next_action = "Complete payment to activate your church, or wait for EcclesiaFlow to activate it."
    elif org.status == "suspended":
        next_action = "This church is suspended. Contact EcclesiaFlow support."
    return {
        "org_status": org.status,
        "verification_status": org.verification_status,
        "activation_source": org.activation_source,
        "is_active": is_active,
        "steps": steps,
        "next_action": next_action,
    }
