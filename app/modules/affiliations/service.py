"""Organisation affiliations — independently registered churches joining a
parent organisation (a parish joining a diocese, a congregation joining a
denomination) without giving up ownership of their data.

The rules:

- Either side may propose; the other side must accept. Nothing becomes
  active on one side's say-so.
- A parent must be a verified organisation, so nobody can claim to be a
  diocese and collect parishes.
- A church has at most one parent at a time, and the chain can't loop.
- The child decides what the parent may see (``grants``), at proposal or on
  acceptance, and can change it at any time. The parent never gets a tenant
  session on the child: it reads only through two database functions that
  return counts and shared gatherings, and only while the grant stands.
- Either side may end an active affiliation. Rows are never deleted, and
  each side's actions are written to its own audit log.
"""

from datetime import UTC, datetime

from prisma import Prisma

from app.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from app.modules.affiliations import repository
from app.modules.affiliations.schemas import (
    AffiliationAccept,
    AffiliationDecision,
    AffiliationGrantsUpdate,
    AffiliationPropose,
)
from app.modules.audit.service import write_audit

_LIVE = ("active", "trial")


def to_read(row: dict, tenant_id: str) -> dict:
    my_role = "parent" if row["parent_org_id"] == tenant_id else "child"
    other = "child" if my_role == "parent" else "parent"
    return {
        **row,
        "my_role": my_role,
        "initiated_by_me": row["initiated_by_org_id"] == tenant_id,
        "counterpart": {
            "id": row[f"{other}_org_id"],
            "name": row[f"{other}_name"],
            "slug": row[f"{other}_slug"],
        },
        "grants": row.get("grants") or [],
    }


async def _audit(db: Prisma, tenant_id: str, user_id: str, action: str, affiliation_id: str, **metadata) -> None:
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        action=f"affiliation.{action}",
        resource_type="organization_affiliation",
        resource_id=affiliation_id,
        metadata=metadata or None,
    )


async def _get(db: Prisma, affiliation_id: str) -> dict:
    row = await repository.get(db, affiliation_id)
    if row is None:
        raise NotFoundError("No such affiliation")
    return row


def _require_verified_parent(parent) -> None:
    if parent.verification_status != "verified":
        raise AppError(
            f"{parent.display_name} isn't a verified organisation yet, so it can't take churches under it",
            code="parent_not_verified",
        )


async def list_affiliations(db: Prisma, tenant_id: str) -> list[dict]:
    return [to_read(r, tenant_id) for r in await repository.list_all(db)]


async def propose(db: Prisma, *, tenant_id: str, user_id: str, payload: AffiliationPropose) -> dict:
    counterpart = await db.organization.find_unique(where={"slug": payload.counterpart_slug})
    if counterpart is None or counterpart.status not in _LIVE:
        raise NotFoundError("No organisation found at this address")
    if counterpart.id == tenant_id:
        raise AppError("A church can't affiliate with itself", code="self_affiliation")

    me = await db.organization.find_unique(where={"id": tenant_id})
    if payload.counterpart_role == "parent":
        child, parent = me, counterpart
    else:
        child, parent = counterpart, me
    _require_verified_parent(parent)
    if await repository.open_for_child(db, child.id) is not None:
        raise ConflictError(
            f"{child.display_name} already has a parent organisation or a pending request", code="has_parent"
        )
    if await repository.would_cycle(db, child.id, parent.id):
        raise ConflictError("That would make the structure loop back on itself", code="cycle")

    affiliation_id = await repository.create(
        db,
        {
            "child_org_id": child.id,
            "parent_org_id": parent.id,
            "initiated_by_org_id": tenant_id,
            "status": "requested",
            "grants": sorted(set(payload.grants)),
            "message": payload.message,
            "requested_by_user_id": user_id,
        },
    )
    await _audit(
        db,
        tenant_id,
        user_id,
        "proposed",
        affiliation_id,
        counterpart_org_id=counterpart.id,
        counterpart_role=payload.counterpart_role,
        grants=sorted(set(payload.grants)),
    )
    return to_read(await _get(db, affiliation_id), tenant_id)


async def accept(db: Prisma, *, tenant_id: str, user_id: str, affiliation_id: str, payload: AffiliationAccept) -> dict:
    row = await _get(db, affiliation_id)
    if row["status"] != "requested":
        raise ConflictError("This request has already been answered", code="invalid_transition")
    if row["initiated_by_org_id"] == tenant_id:
        raise ForbiddenError("The other organisation has to accept this request", code="not_your_decision")

    grants = sorted(set(row["grants"] or []))
    if payload.grants is not None:
        if row["child_org_id"] != tenant_id:
            raise ForbiddenError("Only the church joining decides what it shares", code="grants_are_childs")
        grants = sorted(set(payload.grants))

    parent = await db.organization.find_unique(where={"id": row["parent_org_id"]})
    child = await db.organization.find_unique(where={"id": row["child_org_id"]})
    if parent.status not in _LIVE or child.status not in _LIVE:
        raise ConflictError("One of the organisations is no longer active", code="org_inactive")
    _require_verified_parent(parent)
    if await repository.would_cycle(db, row["child_org_id"], row["parent_org_id"]):
        raise ConflictError("That would make the structure loop back on itself", code="cycle")

    now = datetime.now(UTC)
    await repository.update(
        db,
        affiliation_id,
        {
            "status": "active",
            "grants": grants,
            "decided_by_user_id": user_id,
            "decided_at": now,
            "decision_note": payload.note,
            "effective_from": now,
        },
    )
    await _audit(db, tenant_id, user_id, "accepted", affiliation_id, grants=grants)
    return to_read(await _get(db, affiliation_id), tenant_id)


async def decline(
    db: Prisma, *, tenant_id: str, user_id: str, affiliation_id: str, payload: AffiliationDecision
) -> dict:
    row = await _get(db, affiliation_id)
    if row["status"] != "requested":
        raise ConflictError("This request has already been answered", code="invalid_transition")
    if row["initiated_by_org_id"] == tenant_id:
        raise ForbiddenError("Withdraw your own request instead", code="not_your_decision")
    await repository.update(
        db,
        affiliation_id,
        {
            "status": "declined",
            "decided_by_user_id": user_id,
            "decided_at": datetime.now(UTC),
            "decision_note": payload.note,
        },
    )
    await _audit(db, tenant_id, user_id, "declined", affiliation_id)
    return to_read(await _get(db, affiliation_id), tenant_id)


async def withdraw(db: Prisma, *, tenant_id: str, user_id: str, affiliation_id: str) -> dict:
    row = await _get(db, affiliation_id)
    if row["status"] != "requested":
        raise ConflictError("This request has already been answered", code="invalid_transition")
    if row["initiated_by_org_id"] != tenant_id:
        raise ForbiddenError("Only the organisation that asked can withdraw the request", code="not_your_decision")
    await repository.update(db, affiliation_id, {"status": "withdrawn", "decided_at": datetime.now(UTC)})
    await _audit(db, tenant_id, user_id, "withdrawn", affiliation_id)
    return to_read(await _get(db, affiliation_id), tenant_id)


async def end(db: Prisma, *, tenant_id: str, user_id: str, affiliation_id: str, payload: AffiliationDecision) -> dict:
    row = await _get(db, affiliation_id)
    if row["status"] != "active":
        raise ConflictError("Only an active affiliation can be ended", code="invalid_transition")
    await repository.update(
        db,
        affiliation_id,
        {"status": "ended", "ended_by_user_id": user_id, "ended_at": datetime.now(UTC), "end_reason": payload.note},
    )
    await _audit(db, tenant_id, user_id, "ended", affiliation_id, reason=payload.note)
    return to_read(await _get(db, affiliation_id), tenant_id)


async def update_grants(
    db: Prisma, *, tenant_id: str, user_id: str, affiliation_id: str, payload: AffiliationGrantsUpdate
) -> dict:
    row = await _get(db, affiliation_id)
    if row["child_org_id"] != tenant_id:
        raise ForbiddenError("Only the church that shares decides what it shares", code="grants_are_childs")
    if row["status"] not in ("requested", "active"):
        raise ConflictError("This affiliation is closed", code="invalid_transition")
    grants = sorted(set(payload.grants))
    await repository.update(db, affiliation_id, {"grants": grants})
    await _audit(db, tenant_id, user_id, "grants_changed", affiliation_id, before=row["grants"], after=grants)
    return to_read(await _get(db, affiliation_id), tenant_id)


async def _shared(db: Prisma, tenant_id: str, affiliation_id: str, grant: str) -> dict:
    row = await _get(db, affiliation_id)
    if row["parent_org_id"] != tenant_id or row["status"] != "active":
        raise NotFoundError("No such affiliated church")
    if grant not in (row["grants"] or []):
        raise ForbiddenError(f"{row['child_name']} hasn't chosen to share this", code="not_shared")
    return row


async def summary(db: Prisma, *, tenant_id: str, affiliation_id: str) -> dict:
    await _shared(db, tenant_id, affiliation_id, "aggregate_stats")
    result = await repository.summary(db, affiliation_id)
    if result is None:  # the database function re-checks everything independently
        raise ForbiddenError("This isn't shared with you", code="not_shared")
    return result


async def published_events(db: Prisma, *, tenant_id: str, affiliation_id: str) -> list[dict]:
    await _shared(db, tenant_id, affiliation_id, "published_events")
    return await repository.published_events(db, affiliation_id)
