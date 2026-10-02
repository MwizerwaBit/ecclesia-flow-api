import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UpdatedAtMixin, UUIDPkMixin


class CertificateTemplate(UUIDPkMixin, TenantMixin, TimestampMixin, UpdatedAtMixin, Base):
    __tablename__ = "certificate_templates"
    __table_args__ = (
        CheckConstraint(
            "category in ('sacramental','membership','recognition','education')",
            name="certificate_templates_category_check",
        ),
        CheckConstraint("status in ('active','draft','archived')", name="certificate_templates_status_check"),
    )

    name: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    background_image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    tokens: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    qr_code_position: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class Certificate(UUIDPkMixin, TenantMixin, Base):
    __tablename__ = "certificates"
    __table_args__ = (UniqueConstraint("tenant_id", "serial_number", name="certificates_tenant_serial_key"),)

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("certificate_templates.id"), nullable=False
    )
    member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("members.id"), nullable=False)
    issued_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    serial_number: Mapped[str] = mapped_column(String, nullable=False)
    qr_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    custom_values: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(String, nullable=True)
