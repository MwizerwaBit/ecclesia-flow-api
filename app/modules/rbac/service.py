from prisma import Prisma

from app.core.exceptions import ConflictError
from app.modules.rbac import repository
from app.modules.rbac.models import ALL_CATALOGUE_PERMISSIONS, PERMISSION_CATALOGUE, SYSTEM_ROLE_PERMISSIONS


async def resolve_permissions(db: Prisma, role_id: str) -> list[str]:
    """A custom role's own checklist REPLACES the system default entirely —
    mirrors useRole.ts's `user?.permissions ?? ROLE_PERMISSIONS[role]` exactly
    (a custom role is a different, explicit list, never a merge)."""
    if role_id in SYSTEM_ROLE_PERMISSIONS:
        return SYSTEM_ROLE_PERMISSIONS[role_id]
    return await repository.get_custom_permissions(db, role_id)


def permission_catalogue() -> list[dict]:
    return PERMISSION_CATALOGUE


async def list_roles(db: Prisma) -> list[dict]:
    return await repository.list_roles_for_tenant(db)


async def create_role(db: Prisma, *, tenant_id: str, name: str, color: str | None, permissions: list[str]) -> dict:
    unknown = set(permissions) - ALL_CATALOGUE_PERMISSIONS
    if unknown:
        raise ConflictError(f"Unknown permission(s): {', '.join(sorted(unknown))}")
    role = await repository.create_custom_role(db, tenant_id=tenant_id, name=name, color=color, permissions=permissions)
    return {
        "id": role.id,
        "tenant_id": role.tenant_id,
        "name": role.name,
        "color": role.color,
        "permissions": permissions,
        "is_system": False,
        "member_count": 0,
        "created_at": role.created_at,
    }
