import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, TimestampMixin, UpdatedAtMixin, UUIDPkMixin


class Household(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "households"

    name: Mapped[str] = mapped_column(String, nullable=False)
    head_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", use_alter=True, name="households_head_member_fk"), nullable=True
    )
    address_line1: Mapped[str | None] = mapped_column(String, nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String, nullable=True)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String, nullable=True)
    total_giving: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)


class Member(UUIDPkMixin, TenantMixin, TimestampMixin, UpdatedAtMixin, Base):
    __tablename__ = "members"
    __table_args__ = (
        CheckConstraint("status in ('active','visitor','inactive','prospect')", name="members_status_check"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    household_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("households.id", ondelete="SET NULL"), nullable=True
    )
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String, nullable=False)
    preferred_name: Mapped[str | None] = mapped_column(String, nullable=True)
    photo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(CITEXT, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    whatsapp: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    envelope_number: Mapped[str | None] = mapped_column(String, nullable=True)
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hierarchy_units.id", ondelete="SET NULL"), nullable=True
    )
    joined_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_seen_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String, nullable=True)
    marital_status: Mapped[str | None] = mapped_column(String, nullable=True)
    occupation: Mapped[str | None] = mapped_column(String, nullable=True)
    address_line1: Mapped[str | None] = mapped_column(String, nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String, nullable=True)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String, nullable=True)
    transferred_to_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id"), nullable=True
    )
    transferred_from_member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id"), nullable=True
    )


class SacramentalRecord(UUIDPkMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "sacramental_records"

    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    officiant_name: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PastoralNote(UUIDPkMixin, TenantMixin, TimestampMixin, UpdatedAtMixin, Base):
    __tablename__ = "pastoral_notes"

    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    author_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class MemberTransfer(UUIDPkMixin, Base):
    """The one legitimately cross-tenant table — see docs/database-design.md
    §6. No TenantMixin: it deliberately references two tenants at once and
    gets its own RLS policy (visible if either side matches)."""

    __tablename__ = "member_transfers"
    __table_args__ = (
        CheckConstraint("from_tenant_id <> to_tenant_id", name="member_transfers_distinct_tenants_check"),
        CheckConstraint(
            "status in ('pending','approved','completed','rejected','canceled')",
            name="member_transfers_status_check",
        ),
        CheckConstraint(
            "history_scope in ('identity_only','include_sacraments','full_history')",
            name="member_transfers_history_scope_check",
        ),
    )

    from_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    to_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    from_member_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("members.id"), nullable=False)
    to_member_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("members.id"), nullable=True)
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    history_scope: Mapped[str] = mapped_column(String, nullable=False, default="identity_only")
    certificate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("certificates.id"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
