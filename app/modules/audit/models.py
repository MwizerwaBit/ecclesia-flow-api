import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPkMixin


class AuditLog(UUIDPkMixin, Base):
    """tenant_id is nullable by design — a platform-level action (impersonation,
    tier change, feature-flag override) is logged with tenant_id=null here AND
    is expected to also be reflected into the affected tenant's own view where
    applicable, matching the "double-logged" requirement in screen-inventory.md
    PA-08. No TenantMixin: a NOT NULL tenant_id would make platform rows
    impossible to represent."""

    __tablename__ = "audit_logs"

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    resource_type: Mapped[str] = mapped_column(String, nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    audit_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    is_impersonated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    impersonated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
