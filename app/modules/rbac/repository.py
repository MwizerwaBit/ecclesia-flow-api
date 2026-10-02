import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac.models import Role, RolePermission


async def get_role(db: AsyncSession, role_id: str | uuid.UUID) -> Role | None:
    result = await db.execute(select(Role).where(Role.id == role_id))
    return result.scalar_one_or_none()


async def get_system_role_by_name(db: AsyncSession, name: str) -> Role | None:
    result = await db.execute(select(Role).where(Role.tenant_id.is_(None), Role.name == name))
    return result.scalar_one_or_none()


async def get_custom_permissions(db: AsyncSession, role_id: str | uuid.UUID) -> list[str]:
    result = await db.execute(select(RolePermission.permission).where(RolePermission.role_id == role_id))
    return [row[0] for row in result.all()]


async def list_roles_for_tenant(db: AsyncSession) -> list[Role]:
    """Tenant RLS self-carve-out (see migration 0002) already restricts this
    to system roles (tenant_id null) plus the current tenant's own custom
    roles — no extra filtering needed here."""
    result = await db.execute(select(Role))
    return list(result.scalars().all())


async def create_custom_role(
    db: AsyncSession, *, tenant_id: uuid.UUID, name: str, color: str | None, permissions: list[str]
) -> Role:
    role = Role(id=uuid.uuid4(), tenant_id=tenant_id, name=name, color=color, is_system=False)
    db.add(role)
    await db.flush()
    db.add_all([RolePermission(role_id=role.id, permission=p) for p in permissions])
    await db.flush()
    return role
