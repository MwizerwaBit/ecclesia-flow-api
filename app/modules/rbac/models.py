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
# The church leader: everything an administrator can do, plus the powers
# that define the church itself (its leadership tree, who administers it).
SYSTEM_ROLE_LEADER_ID = "00000000-0000-0000-0000-000000000004"

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
        "giving:read_own",
        "notifications:read",
        "members:read",
        "members:create",
        "members:update",
        "groups:read",
        "groups:manage",
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
        "portal:view",
        "giving:read_own",
        "notifications:read",
        "members:read",
        "members:create",
        "members:update",
        "members:export",
        "groups:read",
        "groups:manage",
        "attendance:read",
        "attendance:create",
        "events:read",
        "events:create",
        "events:update",
        "events:review",
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
        "billing:manage",
        "org:documents",
        "profile:read",
        "profile:update_own",
        "pastoral_notes:read",
        "pastoral_notes:write",
        "network:read",
        "parish_comparison:read",
        "domain:manage",
        "white_label:manage",
        "affiliations:read",
    ],
}

SYSTEM_ROLE_PERMISSIONS[SYSTEM_ROLE_LEADER_ID] = [
    *SYSTEM_ROLE_PERMISSIONS[SYSTEM_ROLE_BOARD_ID],
    "leadership:manage",
    "admins:manage",
    "affiliations:manage",
]

# Leader-only: never part of the administrator role, and (by the no-escalation
# rule) only grantable by someone who already holds them — i.e. the leader.
LEADER_ONLY_PERMISSIONS = frozenset({"leadership:manage", "admins:manage", "affiliations:manage"})

SYSTEM_ROLE_NAMES = {
    SYSTEM_ROLE_MEMBER_ID: "member",
    SYSTEM_ROLE_STAFF_ID: "staff",
    SYSTEM_ROLE_BOARD_ID: "board",
    SYSTEM_ROLE_LEADER_ID: "leader",
}

#: How each system role is shown to people. "board" is the church's
#: administrator / maintainer — not necessarily its leader.
SYSTEM_ROLE_LABELS = {
    "member": "Member",
    "staff": "Staff",
    "board": "Administrator",
    "leader": "Church leader",
}

# Mirrors src/mocks/comms.mock.ts's PERMISSION_CATALOGUE exactly — the
# checklist CustomRoleBuilder.tsx renders. Grouped by module purely for
# display; the permission strings themselves are what the backend enforces.
PERMISSION_CATALOGUE: list[dict] = [
    {
        "module": "People",
        "description": "Member records, visitors and households",
        "permissions": [
            {"key": "members:read", "label": "View member records"},
            {"key": "members:create", "label": "Add members and visitors"},
            {"key": "members:update", "label": "Edit member records"},
            {"key": "members:export", "label": "Export the directory", "sensitive": True},
            {"key": "pastoral_notes:read", "label": "Read pastoral notes", "sensitive": True},
            {"key": "pastoral_notes:write", "label": "Write pastoral notes", "sensitive": True},
            {"key": "groups:read", "label": "View groups and their rosters"},
            {"key": "groups:manage", "label": "Create groups and manage who is in them"},
        ],
    },
    {
        "module": "Attendance",
        "description": "Service registers and headcounts",
        "permissions": [
            {"key": "attendance:read", "label": "View attendance"},
            {"key": "attendance:create", "label": "Record attendance"},
        ],
    },
    {
        "module": "Gatherings",
        "description": "Services, meetings and events",
        "permissions": [
            {"key": "events:read", "label": "View gatherings"},
            {"key": "events:create", "label": "Create gatherings"},
            {"key": "events:update", "label": "Edit gatherings"},
            {"key": "events:review", "label": "Review gatherings before they are published"},
        ],
    },
    {
        "module": "Giving",
        "description": "Offerings, funds and pledges",
        "permissions": [
            {"key": "finance:read", "label": "View giving records", "sensitive": True},
            {"key": "finance:create", "label": "Record giving"},
            {"key": "finance:update", "label": "Edit and void giving", "sensitive": True},
            {"key": "finance:export", "label": "Export financial reports", "sensitive": True},
        ],
    },
    {
        "module": "Communications",
        "description": "Announcements and message templates",
        "permissions": [
            {"key": "announcements:read", "label": "View announcements"},
            {"key": "announcements:create", "label": "Publish announcements"},
        ],
    },
    {
        "module": "Administration",
        "description": "Team, roles and organisation settings",
        "permissions": [
            {"key": "team:read", "label": "View the team"},
            {"key": "team:invite", "label": "Invite team members", "sensitive": True},
            {"key": "roles:create", "label": "Create and edit roles", "sensitive": True},
            {"key": "org:settings", "label": "Change organisation settings", "sensitive": True},
            {"key": "billing:manage", "label": "Manage the subscription and payments", "sensitive": True},
            {"key": "org:documents", "label": "Upload official registration documents", "sensitive": True},
            {"key": "leadership:manage", "label": "Shape the leadership structure", "sensitive": True},
            {"key": "admins:manage", "label": "Appoint and remove administrators", "sensitive": True},
            {"key": "affiliations:read", "label": "See parent and affiliated organisations"},
            {
                "key": "affiliations:manage",
                "label": "Join or leave a parent organisation and decide what it can see",
                "sensitive": True,
            },
        ],
    },
]

ALL_CATALOGUE_PERMISSIONS: set[str] = {p["key"] for group in PERMISSION_CATALOGUE for p in group["permissions"]}
