import uuid
from datetime import datetime

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantMixin, UUIDPkMixin


class User(UUIDPkMixin, Base):
    """Global login identity — NOT tenant-scoped. Which churches it belongs
    to is a separate join table (TenantMembership) so one login can move, or
    belong to several churches, without ever duplicating the account."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String, nullable=False)
    photo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    platform_admin_level: Mapped[str | None] = mapped_column(String, nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mfa_secret: Mapped[str | None] = mapped_column(String, nullable=True)  # encrypted at rest
    mfa_backup_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)  # hashed
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TenantMembership(UUIDPkMixin, TenantMixin, Base):
    """One row per (user, church) — the whole mechanism for "a user can
    change church": switching = picking a different row, zero data movement."""

    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id", name="tenant_memberships_user_tenant_key"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id"), nullable=False)
    unit_scope_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hierarchy_units.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Not in the original database-design.md — added to back the frontend's
    # TeamMember.isLeader (Phase 4 leadership-transfer flow), a field the
    # design doc predates. Distinct from role/permissions: the leader is
    # always a 'staff' or 'board' role holder, never a permission by itself.
    is_leader: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RefreshToken(UUIDPkMixin, Base):
    """Opaque refresh tokens, stored only as a SHA-256 hash — rotated on
    every use (the old row is revoked, a new one issued) so a replayed stale
    token is detectable and the whole family can be revoked on reuse."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    membership_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant_memberships.id", ondelete="CASCADE"), nullable=True
    )
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
