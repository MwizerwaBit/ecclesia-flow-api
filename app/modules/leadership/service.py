"""The church's leadership structure — named, nested positions that carry
access.

Every church organises itself differently (Bishop → Vicar → Warden; Senior
Pastor → Elder → Deacon; Imam → Committee…). A position is a title in a tree;
it may also grant an RBAC role and a branch scope, so "make Grace our Youth
Pastor for Eastside" is one action that also gives her exactly the access the
church attached to that post — and removing her takes it away again.

Rules that keep this safe:
  - only the leader shapes the structure (leadership:manage, leader-only)
  - a position can't grant the leader or administrator roles — those have
    their own flows — nor any permission the leader doesn't hold
  - the leader and administrators may hold honorific positions only, so a
    position can never silently change what they can do
  - one position per person, so their access always has one clear source
"""

from prisma import Prisma

from app.core.authz import Scope, ensure_grantable
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.modules.audit.service import write_audit
from app.modules.leadership.schemas import PositionCreate, PositionUpdate
from app.modules.rbac.models import SYSTEM_ROLE_BOARD_ID, SYSTEM_ROLE_LEADER_ID, SYSTEM_ROLE_STAFF_ID
from app.modules.rbac.service import resolve_permissions

PROTECTED_ROLES = {SYSTEM_ROLE_LEADER_ID, SYSTEM_ROLE_BOARD_ID}


async def list_positions(db: Prisma) -> list[dict]:
    positions = await db.query_raw(
        """
        select p.id, p.parent_id, p.title, p.description, p.role_id, r.name as role_name,
               p.unit_id, hu.name as unit_name, p.sort_order
        from leadership_positions p
        left join roles r on r.id = p.role_id
        left join hierarchy_units hu on hu.id = p.unit_id
        order by p.sort_order, p.title
        """
    )
    holders = await db.query_raw(
        """
        select a.position_id, a.membership_id, a.assigned_at, u.id as user_id, u.first_name, u.last_name,
               u.email, u.photo_url
        from leadership_assignments a
        join tenant_memberships tm on tm.id = a.membership_id
        join users u on u.id = tm.user_id
        order by u.last_name, u.first_name
        """
    )
    by_position: dict[str, list[dict]] = {}
    for h in holders:
        by_position.setdefault(h["position_id"], []).append(h)
    return [{**p, "holders": by_position.get(p["id"], [])} for p in positions]


async def _check_role(db: Prisma, scope: Scope, role_id: str | None) -> None:
    if role_id is None:
        return
    if role_id in PROTECTED_ROLES:
        raise ForbiddenError(
            "Positions can't grant the leader or administrator role — use Church leadership for those",
            code="protected_role",
        )
    if await db.role.find_unique(where={"id": role_id}) is None:
        raise NotFoundError("No such role")
    ensure_grantable(scope, await resolve_permissions(db, role_id))


async def _check_parent(db: Prisma, position_id: str | None, parent_id: str | None) -> None:
    if parent_id is None:
        return
    if await db.leadershipposition.find_unique(where={"id": parent_id}) is None:
        raise NotFoundError("No such parent position")
    # Walk up from the proposed parent: reaching ourselves would make a cycle.
    current = parent_id
    while current is not None and position_id is not None:
        if current == position_id:
            raise ConflictError("A position can't sit underneath itself")
        parent = await db.leadershipposition.find_unique(where={"id": current})
        current = parent.parent_id if parent else None


async def create_position(db: Prisma, tenant_id: str, scope: Scope, payload: PositionCreate) -> dict:
    await _check_role(db, scope, payload.role_id)
    await _check_parent(db, None, payload.parent_id)
    position = await db.leadershipposition.create(
        data={**payload.model_dump(exclude_none=True), "tenant_id": tenant_id}
    )
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=scope.user_id,
        action="leadership.position_created",
        resource_type="leadership_position",
        resource_id=position.id,
        metadata={"title": payload.title},
    )
    return next(p for p in await list_positions(db) if p["id"] == position.id)


async def update_position(db: Prisma, tenant_id: str, scope: Scope, position_id: str, payload: PositionUpdate) -> dict:
    position = await db.leadershipposition.find_unique(where={"id": position_id})
    if position is None:
        raise NotFoundError("No such position")
    data = payload.model_dump(exclude_unset=True)
    if "role_id" in data:
        await _check_role(db, scope, data["role_id"])
    if "parent_id" in data:
        await _check_parent(db, position_id, data["parent_id"])
    await db.leadershipposition.update(where={"id": position_id}, data=data)
    # Holders follow the position's access.
    if "role_id" in data or "unit_id" in data:
        updated = await db.leadershipposition.find_unique(where={"id": position_id})
        for a in await db.leadershipassignment.find_many(where={"position_id": position_id}):
            await _apply_position_access(db, a.membership_id, updated.role_id, updated.unit_id)
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=scope.user_id,
        action="leadership.position_updated",
        resource_type="leadership_position",
        resource_id=position_id,
        metadata={"fields": sorted(data)},
    )
    return next(p for p in await list_positions(db) if p["id"] == position_id)


async def delete_position(db: Prisma, tenant_id: str, scope: Scope, position_id: str) -> None:
    position = await db.leadershipposition.find_unique(where={"id": position_id})
    if position is None:
        raise NotFoundError("No such position")
    if await db.leadershipposition.count(where={"parent_id": position_id}):
        raise ConflictError("Move or remove the positions under this one first")
    for a in await db.leadershipassignment.find_many(where={"position_id": position_id}):
        await unassign(db, tenant_id, scope, position_id, a.membership_id)
    await db.leadershipposition.delete(where={"id": position_id})
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=scope.user_id,
        action="leadership.position_deleted",
        resource_type="leadership_position",
        resource_id=position_id,
        metadata={"title": position.title},
    )


async def _apply_position_access(db: Prisma, membership_id: str, role_id: str | None, unit_id: str | None) -> None:
    if role_id is None:
        return  # honorific — access unchanged
    await db.tenantmembership.update(where={"id": membership_id}, data={"role_id": role_id, "unit_scope_id": unit_id})


async def assign(db: Prisma, tenant_id: str, scope: Scope, position_id: str, membership_id: str) -> dict:
    position = await db.leadershipposition.find_unique(where={"id": position_id})
    if position is None:
        raise NotFoundError("No such position")
    membership = await db.tenantmembership.find_unique(where={"id": membership_id})
    if membership is None or membership.status != "active":
        raise NotFoundError("No such active team member")
    if position.role_id is not None and (membership.is_leader or membership.role_id in PROTECTED_ROLES):
        raise ConflictError(
            "The leader and administrators can only hold positions that don't change access", code="protected_holder"
        )
    if position.role_id is not None:
        ensure_grantable(scope, await resolve_permissions(db, position.role_id))

    # One position per person: moving someone replaces their old post.
    previous = await db.leadershipassignment.find_unique(where={"membership_id": membership_id})
    if previous is not None:
        await db.leadershipassignment.delete(where={"id": previous.id})
    await db.leadershipassignment.create(
        data={
            "tenant_id": tenant_id,
            "position_id": position_id,
            "membership_id": membership_id,
            "assigned_by_user_id": scope.user_id,
        }
    )
    await _apply_position_access(db, membership_id, position.role_id, position.unit_id)
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=scope.user_id,
        action="leadership.assigned",
        resource_type="leadership_position",
        resource_id=position_id,
        metadata={"membership_id": membership_id},
    )
    return next(p for p in await list_positions(db) if p["id"] == position_id)


async def unassign(db: Prisma, tenant_id: str, scope: Scope, position_id: str, membership_id: str) -> None:
    assignment = await db.leadershipassignment.find_first(
        where={"position_id": position_id, "membership_id": membership_id}
    )
    if assignment is None:
        raise NotFoundError("That person doesn't hold this position")
    position = await db.leadershipposition.find_unique(where={"id": position_id})
    await db.leadershipassignment.delete(where={"id": assignment.id})
    if position and position.role_id is not None:
        # Leaving the post takes its access with it: back to ordinary staff.
        await db.tenantmembership.update(
            where={"id": membership_id}, data={"role_id": SYSTEM_ROLE_STAFF_ID, "unit_scope_id": None}
        )
    await write_audit(
        db,
        tenant_id=tenant_id,
        actor_user_id=scope.user_id,
        action="leadership.unassigned",
        resource_type="leadership_position",
        resource_id=position_id,
        metadata={"membership_id": membership_id},
    )
