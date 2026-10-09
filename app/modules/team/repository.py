from datetime import UTC, datetime

from prisma import Prisma


async def list_team(db: Prisma) -> list[dict]:
    return await db.query_raw(
        """
        select tm.id, tm.tenant_id, tm.user_id, u.first_name, u.last_name, u.email, u.photo_url,
               tm.role_id, r.name as role_name, r.color as role_color,
               tm.unit_scope_id as unit_scope, hu.name as unit_scope_name,
               u.mfa_enabled, tm.last_active_at, tm.invited_at, tm.accepted_at, tm.is_leader
        from tenant_memberships tm
        join users u on u.id = tm.user_id
        join roles r on r.id = tm.role_id
        left join hierarchy_units hu on hu.id = tm.unit_scope_id
        where tm.tenant_id = current_tenant_id()
        order by tm.is_leader desc, tm.accepted_at asc nulls last
        """
    )


async def get_membership_row(db: Prisma, membership_id: str) -> dict | None:
    rows = await db.query_raw(
        """
        select tm.id, tm.tenant_id, tm.user_id, u.first_name, u.last_name, u.email, u.photo_url,
               tm.role_id, r.name as role_name, r.color as role_color,
               tm.unit_scope_id as unit_scope, hu.name as unit_scope_name,
               u.mfa_enabled, tm.last_active_at, tm.invited_at, tm.accepted_at, tm.is_leader, tm.status
        from tenant_memberships tm
        join users u on u.id = tm.user_id
        join roles r on r.id = tm.role_id
        left join hierarchy_units hu on hu.id = tm.unit_scope_id
        where tm.id = $1::uuid and tm.tenant_id = current_tenant_id()
        """,
        membership_id,
    )
    return rows[0] if rows else None


async def get_leader(db: Prisma) -> dict | None:
    rows = await db.query_raw(
        """
        select tm.id, tm.tenant_id, tm.user_id, u.first_name, u.last_name, u.email, u.photo_url,
               tm.role_id, r.name as role_name, r.color as role_color,
               tm.unit_scope_id as unit_scope, hu.name as unit_scope_name,
               u.mfa_enabled, tm.last_active_at, tm.invited_at, tm.accepted_at, tm.is_leader
        from tenant_memberships tm
        join users u on u.id = tm.user_id
        join roles r on r.id = tm.role_id
        left join hierarchy_units hu on hu.id = tm.unit_scope_id
        where tm.tenant_id = current_tenant_id() and tm.is_leader = true
        """
    )
    return rows[0] if rows else None


async def find_user_by_email(db: Prisma, email: str):
    return await db.user.find_unique(where={"email": email})


async def create_invited_user(db: Prisma, *, email: str, first_name: str, last_name: str):
    return await db.user.create(data={"email": email, "first_name": first_name, "last_name": last_name})


async def create_membership(
    db: Prisma,
    *,
    user_id: str,
    tenant_id: str,
    role_id: str,
    unit_scope_id: str | None,
    invite_token_hash: str,
    invite_expires_at: datetime,
    is_leader: bool = False,
):
    now = datetime.now(UTC)
    return await db.tenantmembership.create(
        data={
            "user_id": user_id,
            "tenant_id": tenant_id,
            "role_id": role_id,
            "unit_scope_id": unit_scope_id,
            "status": "invited",
            "invited_at": now,
            "invite_token_hash": invite_token_hash,
            "invite_expires_at": invite_expires_at,
            "is_leader": is_leader,
        }
    )


async def role_exists(db: Prisma, role_id: str) -> bool:
    # System roles (tenant_id null) and this church's custom roles; RLS hides others.
    return await db.role.find_unique(where={"id": role_id}) is not None


async def unit_exists(db: Prisma, unit_id: str) -> bool:
    return await db.hierarchyunit.find_unique(where={"id": unit_id}) is not None


async def update_assignment(db: Prisma, membership_id: str, data: dict) -> None:
    await db.tenantmembership.update(where={"id": membership_id}, data=data)


# ───────────────────────────── Leadership transfer ─────────────────────────────


async def get_active_transfer(db: Prisma, tenant_id: str) -> dict | None:
    rows = await db.query_raw(
        """
        select lt.*, ol.user_id as outgoing_user_id, nm.user_id as nominee_user_id
        from leadership_transfers lt
        join tenant_memberships ol on ol.id = lt.outgoing_leader_membership_id
        join tenant_memberships nm on nm.id = lt.nominee_membership_id
        where lt.tenant_id = $1::uuid and lt.status = 'pending_approvals'
        order by lt.initiated_at desc
        limit 1
        """,
        tenant_id,
    )
    return rows[0] if rows else None


async def get_transfer_by_id(db: Prisma, transfer_id: str) -> dict | None:
    rows = await db.query_raw("select * from leadership_transfers where id = $1::uuid", transfer_id)
    return rows[0] if rows else None


async def list_active_membership_ids_excluding(db: Prisma, tenant_id: str, exclude_ids: list[str]) -> list[str]:
    rows = await db.query_raw(
        """
        select id from tenant_memberships
        where tenant_id = $1::uuid and status = 'active' and not (id = any($2::uuid[]))
        """,
        tenant_id,
        exclude_ids,
    )
    return [r["id"] for r in rows]


async def create_transfer(
    db: Prisma,
    *,
    tenant_id: str,
    outgoing_leader_membership_id: str,
    nominee_membership_id: str,
    initiated_by_membership_id: str,
    eligible_approver_ids: list[str],
) -> dict:
    now = datetime.now(UTC)
    row = await db.leadershiptransfer.create(
        data={
            "tenant_id": tenant_id,
            "outgoing_leader_membership_id": outgoing_leader_membership_id,
            "nominee_membership_id": nominee_membership_id,
            "initiated_by_membership_id": initiated_by_membership_id,
            "mfa_verified_at": now,
            "eligible_approver_ids": eligible_approver_ids,
            "required_approvals": 2,
        }
    )
    return await get_transfer_by_id(db, row.id)


async def add_approval(db: Prisma, transfer_id: str, approvals: list[dict], *, status: str, completed: bool) -> None:
    data = {"approvals": approvals, "status": status}
    if completed:
        data["completed_at"] = datetime.now(UTC)
    await db.leadershiptransfer.update(where={"id": transfer_id}, data=data)


async def cancel_transfer(db: Prisma, transfer_id: str) -> None:
    await db.leadershiptransfer.update(
        where={"id": transfer_id}, data={"status": "canceled", "canceled_at": datetime.now(UTC)}
    )


async def complete_leader_flip(
    db: Prisma, *, tenant_id: str, outgoing_membership_id: str, nominee_membership_id: str
) -> None:
    # The flag and the role move together: the outgoing leader stays on as an
    # administrator, the nominee takes the leader role (and any position that
    # granted them other access is released). Order matters for the
    # one-leader-per-church unique index.
    from app.modules.rbac.models import SYSTEM_ROLE_BOARD_ID, SYSTEM_ROLE_LEADER_ID

    await db.tenantmembership.update_many(
        where={"tenant_id": tenant_id, "id": outgoing_membership_id},
        data={"is_leader": False, "role_id": SYSTEM_ROLE_BOARD_ID, "unit_scope_id": None},
    )
    await db.leadershipassignment.delete_many(where={"membership_id": nominee_membership_id})
    await db.tenantmembership.update_many(
        where={"tenant_id": tenant_id, "id": nominee_membership_id},
        data={"is_leader": True, "role_id": SYSTEM_ROLE_LEADER_ID, "unit_scope_id": None},
    )
