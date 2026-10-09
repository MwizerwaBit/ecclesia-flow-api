"""Organisation lifecycle transitions — the only code that changes
`organizations.status`. Used by billing webhooks (activation on payment) and by
the platform admin console (manual activation, suspension). Every transition
is audited."""

from datetime import UTC, datetime

from prisma import Prisma

from app.core.exceptions import ConflictError, NotFoundError
from app.modules.audit.service import write_audit

#: Which transitions are allowed, from → to.
TRANSITIONS: dict[str, set[str]] = {
    "pending": {"active", "canceled"},
    "trial": {"active", "suspended", "canceled"},
    "active": {"suspended", "canceled"},
    "suspended": {"active", "canceled"},
    "canceled": set(),
}


async def transition(
    db: Prisma,
    org_id: str,
    to_status: str,
    *,
    actor_user_id: str | None,
    source: str | None = None,
    note: str | None = None,
    tier: str | None = None,
) -> None:
    org = await db.organization.find_unique(where={"id": org_id})
    if org is None:
        raise NotFoundError("No such organisation")
    if org.status == to_status and to_status == "active":
        # Paying again while active (e.g. a plan change) is not a transition.
        if tier and tier != org.tier:
            await db.organization.update(where={"id": org_id}, data={"tier": tier})
        return
    if to_status not in TRANSITIONS.get(org.status, set()):
        raise ConflictError(f"A {org.status} organisation can't become {to_status}")

    data: dict = {"status": to_status}
    if to_status == "active":
        data.update(activated_at=datetime.now(UTC), activation_source=source, activation_note=note)
    if tier:
        data["tier"] = tier
    await db.organization.update(where={"id": org_id}, data=data)
    await write_audit(
        db,
        tenant_id=org_id,
        actor_user_id=actor_user_id,
        action=f"org.{to_status}",
        resource_type="organization",
        resource_id=org_id,
        metadata={"from": org.status, "source": source, "note": note, "tier": tier},
    )
