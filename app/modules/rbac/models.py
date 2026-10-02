"""System-role constants — not a models module anymore (Prisma generates
`prisma.models.Role`/`RolePermission` from prisma/schema.prisma), kept at
this import path because the migration SQL
(prisma/migrations/20261002090126_init/migration.sql) and
app/modules/rbac/service.py both import it from here.
"""

# The three system roles' ids are fixed so every migration/seed/test refers
# to the same rows rather than looking them up by name each time.
SYSTEM_ROLE_MEMBER_ID = "00000000-0000-0000-0000-000000000001"
SYSTEM_ROLE_STAFF_ID = "00000000-0000-0000-0000-000000000002"
SYSTEM_ROLE_BOARD_ID = "00000000-0000-0000-0000-000000000003"

# Mirrors src/hooks/useRole.ts's ROLE_PERMISSIONS exactly — enforcement must
# never silently diverge from what the frontend already assumes a role can do.
SYSTEM_ROLE_PERMISSIONS: dict[str, list[str]] = {
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
