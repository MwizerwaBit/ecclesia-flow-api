import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPkMixin

# The three system roles' ids are fixed so every migration/seed/test refers to
# the same rows rather than looking them up by name each time.
SYSTEM_ROLE_MEMBER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
SYSTEM_ROLE_STAFF_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
SYSTEM_ROLE_BOARD_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")

# Mirrors src/hooks/useRole.ts's ROLE_PERMISSIONS exactly — enforcement must
# never silently diverge from what the frontend already assumes a role can do.
SYSTEM_ROLE_PERMISSIONS: dict[uuid.UUID, list[str]] = {
    SYSTEM_ROLE_MEMBER_ID: [
        "portal:view",
        "profile:read",
        "profile:update_own",
        "events:read",
        "announcements:read",
        "giving:read_own",
        "notifications:read",
    ],
    SYSTEM_ROLE_STAFF_ID: [
        "portal:view",
        "members:read",
        "members:create",
        "members:update",
        "attendance:read",
        "attendance:create",
        "events:read",
        "events:create",
        "events:update",
        "finance:read",
        "finance:create",
        "finance:update",
        "announcements:read",
        "announcements:create",
        "certificates:read",
        "certificates:create",
        "dashboard:view",
        "profile:read",
        "profile:update_own",
    ],
    SYSTEM_ROLE_BOARD_ID: [
        "members:read",
        "members:create",
        "members:update",
        "members:export",
        "attendance:read",
        "attendance:create",
        "events:read",
        "events:create",
        "events:update",
        "finance:read",
        "finance:create",
        "finance:update",
        "finance:export",
        "announcements:read",
        "announcements:create",
        "certificates:read",
        "certificates:create",
        "dashboard:view",
        "hierarchy:read",
        "hierarchy:update",
        "team:read",
        "team:invite",
        "roles:read",
        "roles:create",
        "analytics:read",
        "org:read",
        "org:settings",
        "profile:read",
        "profile:update_own",
        "pastoral_notes:read",
        "pastoral_notes:write",
        "network:read",
        "parish_comparison:read",
        "domain:manage",
        "white_label:manage",
    ],
}

SYSTEM_ROLE_NAMES = {
    SYSTEM_ROLE_MEMBER_ID: "member",
    SYSTEM_ROLE_STAFF_ID: "staff",
    SYSTEM_ROLE_BOARD_ID: "board",
}


class Role(UUIDPkMixin, TimestampMixin, Base):
    """System roles (tenant_id null, seeded once) and tenant-defined custom
    roles (CustomRoleBuilder) are the same table — a `can('finance:read')`
    check never needs to know which kind of role granted it."""

    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="roles_tenant_name_key"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    color: Mapped[str | None] = mapped_column(String, nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(String, primary_key=True)
