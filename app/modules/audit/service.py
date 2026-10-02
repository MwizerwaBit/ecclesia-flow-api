import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog


async def write_audit(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None = None,
    metadata: dict | None = None,
    ip_address: str | None = None,
    is_impersonated: bool = False,
    impersonated_by_user_id: uuid.UUID | None = None,
) -> None:
    db.add(
        AuditLog(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            audit_metadata=metadata,
            ip_address=ip_address,
            is_impersonated=is_impersonated,
            impersonated_by_user_id=impersonated_by_user_id,
            created_at=datetime.now(UTC),
        )
    )
    await db.flush()
