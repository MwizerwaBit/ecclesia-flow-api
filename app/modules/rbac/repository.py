from prisma import Prisma
from prisma.models import Role


async def get_role(db: Prisma, role_id: str) -> Role | None:
    return await db.role.find_unique(where={"id": role_id})


async def get_system_role_by_name(db: Prisma, name: str) -> Role | None:
    return await db.role.find_first(where={"tenant_id": None, "name": name})


async def get_custom_permissions(db: Prisma, role_id: str) -> list[str]:
    rows = await db.rolepermission.find_many(where={"role_id": role_id})
    return [r.permission for r in rows]


async def list_roles_for_tenant(db: Prisma) -> list[dict]:
    """Tenant RLS self-carve-out (migration's `roles` policy) already
    restricts this to system roles (tenant_id null) plus the current
    tenant's own custom roles — no extra filtering needed here.

    member_count is scoped to the CURRENT tenant even for a system role
    (e.g. "staff" has N members here, a different N at another church) —
    a plain join rather than a role-level stored count.
    """
    return await db.query_raw(
        """
        select r.id, r.tenant_id, r.name, r.color, r.is_system, r.created_at,
               coalesce(array_agg(rp.permission) filter (where rp.permission is not null), '{}') as permissions,
               (select count(*) from tenant_memberships tm
                 where tm.role_id = r.id and tm.tenant_id = current_tenant_id())::int as member_count
        from roles r
        left join role_permissions rp on rp.role_id = r.id
        group by r.id
        order by r.is_system desc, r.created_at asc
        """
    )


async def create_custom_role(
    db: Prisma, *, tenant_id: str, name: str, color: str | None, permissions: list[str]
) -> Role:
    role = await db.role.create(data={"tenant_id": tenant_id, "name": name, "color": color, "is_system": False})
    if permissions:
        await db.rolepermission.create_many(data=[{"role_id": role.id, "permission": p} for p in permissions])
    return role


async def count_members_with_role(db: Prisma, role_id: str, tenant_id: str) -> int:
    return await db.tenantmembership.count(where={"role_id": role_id, "tenant_id": tenant_id})
