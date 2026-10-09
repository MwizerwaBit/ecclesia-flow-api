import secrets
from datetime import UTC, datetime, timedelta

from prisma import Prisma

from app.core.authz import Scope, ensure_grantable
from app.core.config import get_settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.security import hash_refresh_token
from app.modules.rbac.models import SYSTEM_ROLE_BOARD_ID, SYSTEM_ROLE_LEADER_ID
from app.modules.rbac.service import resolve_permissions
from app.modules.team import repository
from app.modules.team.schemas import InviteStaffPayload, UpdateAssignmentPayload


def _unit_scope_out(row: dict) -> dict:
    out = dict(row)
    out["unit_scope"] = row.get("unit_scope") or "all"
    return out


async def list_team(db: Prisma) -> list[dict]:
    rows = await repository.list_team(db)
    return [_unit_scope_out(r) for r in rows]


async def get_leader(db: Prisma) -> dict | None:
    row = await repository.get_leader(db)
    return _unit_scope_out(row) if row else None


async def _check_assignable(db: Prisma, scope: Scope, *, role_id: str | None, unit_scope: str | None) -> str | None:
    """RBAC + ABAC for handing someone access. Returns the unit scope to store.

    - the role must exist in this church and grant nothing the actor lacks
    - a branch-scoped actor can only give access inside their own branch
      ("all" — whole church — is not theirs to give)"""
    if role_id is not None:
        if not await repository.role_exists(db, role_id):
            raise NotFoundError("No such role")
        ensure_grantable(scope, await resolve_permissions(db, role_id))

    requested = None if unit_scope in (None, "all") else unit_scope
    if requested is not None and not await repository.unit_exists(db, requested):
        raise NotFoundError("No such unit")
    return scope.assignable_unit(requested)


async def invite(db: Prisma, tenant_id: str, scope: Scope, payload: InviteStaffPayload) -> dict:
    """Invite staff with any role the inviter is allowed to grant."""
    unit_scope_id = await _check_assignable(db, scope, role_id=payload.role_id, unit_scope=payload.unit_scope)
    if payload.role_id in (SYSTEM_ROLE_BOARD_ID, SYSTEM_ROLE_LEADER_ID):
        # Administrators and the leader are appointed through their own flows
        # (org onboarding / leadership transfer), never as ordinary staff.
        raise ForbiddenError("Appoint administrators from Church leadership", code="leader_only")
    local_part = payload.email.split("@")[0]
    return await invite_with_role(
        db,
        tenant_id=tenant_id,
        email=payload.email,
        first_name=local_part.capitalize(),
        last_name="",
        role_id=payload.role_id,
        unit_scope_id=unit_scope_id,
    )


async def invite_with_role(
    db: Prisma,
    *,
    tenant_id: str,
    email: str,
    first_name: str,
    last_name: str,
    role_id: str,
    unit_scope_id: str | None,
    is_leader: bool = False,
) -> dict:
    """Creates an invited membership and its single-use link. Callers have
    already decided the inviter may grant this role — this function only
    performs the invitation."""
    user = await repository.find_user_by_email(db, email.lower())
    if user is None:
        user = await repository.create_invited_user(db, email=email.lower(), first_name=first_name, last_name=last_name)

    # "<tenant>.<secret>" — see identity.service.accept_invite. Only the hash is stored.
    settings = get_settings()
    raw_token = f"{tenant_id}.{secrets.token_urlsafe(32)}"
    membership = await repository.create_membership(
        db,
        user_id=user.id,
        tenant_id=tenant_id,
        role_id=role_id,
        unit_scope_id=unit_scope_id,
        invite_token_hash=hash_refresh_token(raw_token),
        invite_expires_at=datetime.now(UTC) + timedelta(hours=settings.invite_ttl_hours),
        is_leader=is_leader,
    )
    row = _unit_scope_out(await repository.get_membership_row(db, membership.id))
    # No email service is wired up yet, so outside production the link is
    # returned for the inviter to pass on. In production it must be emailed.
    if not settings.is_production:
        row["invite_url"] = f"{settings.frontend_url}/accept-invite#{raw_token}"
    return row


async def update_assignment(
    db: Prisma, membership_id: str, scope: Scope, actor_membership_id: str | None, payload: UpdateAssignmentPayload
) -> dict:
    existing = await repository.get_membership_row(db, membership_id)
    if existing is None:
        raise NotFoundError("No such team member")
    if membership_id == actor_membership_id:
        raise ForbiddenError("You can't change your own access", code="self_assignment")
    if existing["is_leader"]:
        raise ForbiddenError("The church leader's access changes only through a leadership transfer")
    # A branch-scoped actor manages only people already inside their branch.
    if scope.is_scoped and not scope.unit_writable(existing.get("unit_scope")):
        raise NotFoundError("No such team member")

    data: dict = {}
    if payload.role_id is not None or payload.unit_scope is not None:
        unit = await _check_assignable(
            db,
            scope,
            role_id=payload.role_id,
            unit_scope=payload.unit_scope if payload.unit_scope is not None else (existing.get("unit_scope") or "all"),
        )
        if payload.role_id is not None:
            data["role_id"] = payload.role_id
        if payload.unit_scope is not None:
            data["unit_scope_id"] = unit
    if payload.status is not None:
        if existing["status"] == "invited":
            raise ConflictError("This person hasn't accepted their invitation yet")
        data["status"] = payload.status
    if data:
        await repository.update_assignment(db, membership_id, data)
    row = await repository.get_membership_row(db, membership_id)
    return _unit_scope_out(row)


# ───────────────────────────── Leadership transfer ─────────────────────────────


async def get_leadership_transfer(db: Prisma, tenant_id: str) -> dict | None:
    return await repository.get_active_transfer(db, tenant_id)


async def request_leadership_transfer(
    db: Prisma, tenant_id: str, requester_user_id: str, nominee_membership_id: str
) -> dict:
    leader = await repository.get_leader(db)
    if leader is None:
        raise ConflictError("This church has no recorded leader to transfer from")
    if leader["user_id"] != requester_user_id:
        raise ForbiddenError("Only the current church leader can initiate a leadership transfer")
    nominee = await repository.get_membership_row(db, nominee_membership_id)
    if nominee is None or nominee["status"] != "active":
        raise NotFoundError("Nominee is not an active team member")
    if nominee["id"] == leader["id"]:
        raise ConflictError("The current leader cannot nominate themselves")

    existing = await repository.get_active_transfer(db, tenant_id)
    if existing is not None:
        raise ConflictError("A leadership transfer is already pending")

    eligible = await repository.list_active_membership_ids_excluding(
        db, tenant_id, [leader["id"], nominee_membership_id]
    )
    return await repository.create_transfer(
        db,
        tenant_id=tenant_id,
        outgoing_leader_membership_id=leader["id"],
        nominee_membership_id=nominee_membership_id,
        initiated_by_membership_id=leader["id"],
        eligible_approver_ids=eligible,
    )


async def approve_leadership_transfer(
    db: Prisma, tenant_id: str, transfer_id: str, approver_membership_id: str
) -> dict:
    transfer = await repository.get_transfer_by_id(db, transfer_id)
    if transfer is None or transfer["status"] != "pending_approvals":
        raise NotFoundError("No pending leadership transfer with that id")
    if approver_membership_id not in transfer["eligible_approver_ids"]:
        raise ForbiddenError("This team member is not eligible to approve this transfer")

    approvals = list(transfer["approvals"])
    if any(a["team_member_id"] == approver_membership_id for a in approvals):
        return transfer

    approver = await repository.get_membership_row(db, approver_membership_id)
    approvals.append(
        {
            "team_member_id": approver_membership_id,
            "name": f"{approver['first_name']} {approver['last_name']}",
            "approved_at": datetime.now(UTC).isoformat(),
        }
    )
    completed = len(approvals) >= transfer["required_approvals"]
    await repository.add_approval(
        db, transfer_id, approvals, status="completed" if completed else "pending_approvals", completed=completed
    )
    if completed:
        await repository.complete_leader_flip(
            db,
            tenant_id=tenant_id,
            outgoing_membership_id=transfer["outgoing_leader_membership_id"],
            nominee_membership_id=transfer["nominee_membership_id"],
        )
    return await repository.get_transfer_by_id(db, transfer_id)


async def cancel_leadership_transfer(db: Prisma, transfer_id: str, requester_user_id: str) -> None:
    transfer = await repository.get_transfer_by_id(db, transfer_id)
    if transfer is None:
        raise NotFoundError("No such leadership transfer")
    # Only the outgoing leader may withdraw their own handover.
    leader = await repository.get_leader(db)
    if leader is None or leader["user_id"] != requester_user_id:
        raise ForbiddenError("Only the current church leader can cancel a leadership transfer")
    await repository.cancel_transfer(db, transfer_id)
