import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rbac import repository
from app.modules.rbac.models import SYSTEM_ROLE_PERMISSIONS


async def resolve_permissions(db: AsyncSession, role_id: uuid.UUID) -> list[str]:
    """A custom role's own checklist REPLACES the system default entirely —
    mirrors useRole.ts's `user?.permissions ?? ROLE_PERMISSIONS[role]` exactly
    (a custom role is a different, explicit list, never a merge)."""
    if role_id in SYSTEM_ROLE_PERMISSIONS:
        return SYSTEM_ROLE_PERMISSIONS[role_id]
    return await repository.get_custom_permissions(db, role_id)
