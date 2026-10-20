# Authorization matrix (SA-04)

The source of truth for permissions is code
(`app/modules/rbac/models.py::PERMISSION_CATALOGUE`,
`SYSTEM_ROLE_PERMISSIONS`) and `src/hooks/useRole.ts`/`src/mocks/comms.mock.ts`
on the frontend, which this file must never silently diverge from. This
document is a generated-by-hand snapshot for review, not a second source —
if it disagrees with the code, the code is right and this file is stale.

Related docs: [THREAT_MODEL.md](THREAT_MODEL.md) ·
[DATA_CLASSIFICATION.md](DATA_CLASSIFICATION.md) ·
[ARCHITECTURE.md](ARCHITECTURE.md) §3 (the isolation model this matrix
assumes) · [ADR-0004](adr/0004-unit-scope-in-the-database.md)

---

## 1. Roles

Four system roles, a strict superset hierarchy: **member ⊂ staff ⊂
administrator (`board`) ⊂ leader**. A church may also define **custom
roles**, built from any subset of the catalogue below that the creator
already holds (`ensure_grantable`, no privilege escalation).

| Role | System id | Shown to users as |
|---|---|---|
| `member` | `...0001` | Member |
| `staff` | `...0002` | Staff |
| `board` | `...0003` | Administrator |
| `leader` | `...0004` | Church leader |

## 2. Permission catalogue

Every permission a role or custom role can hold, grouped as the frontend's
`CustomRoleBuilder.tsx` shows them. **Sensitive** permissions additionally
require an **MFA-verified session** to use, even if the token itself holds
them (`MFA_REQUIRED_PERMISSIONS`, `app/core/permissions.py`) — marked **MFA**
below. A role column shows **✓** when that system role is granted the
permission by default (`SYSTEM_ROLE_PERMISSIONS`).

### People — member records, visitors and households

| Permission | Sensitive | MFA | Member | Staff | Admin | Leader |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `members:read` | | | | ✓ | ✓ | ✓ |
| `members:create` | | | | ✓ | ✓ | ✓ |
| `members:update` | | | | ✓ | ✓ | ✓ |
| `members:export` | ✓ | ✓ | | | ✓ | ✓ |
| `pastoral_notes:read` | ✓ | | | | ✓ | ✓ |
| `pastoral_notes:write` | ✓ | | | | ✓ | ✓ |
| `groups:read` | | | | ✓ | ✓ | ✓ |
| `groups:manage` | | | | ✓ | ✓ | ✓ |

### Attendance — service registers and headcounts

| Permission | Sensitive | MFA | Member | Staff | Admin | Leader |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `attendance:read` | | | | ✓ | ✓ | ✓ |
| `attendance:create` | | | | ✓ | ✓ | ✓ |

### Gatherings — services, meetings and events

| Permission | Sensitive | MFA | Member | Staff | Admin | Leader |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `events:read` | | | ✓ | ✓ | ✓ | ✓ |
| `events:create` | | | | ✓ | ✓ | ✓ |
| `events:update` | | | | ✓ | ✓ | ✓ |
| `events:review` | | | | | ✓ | ✓ |

### Giving — offerings, funds and pledges

| Permission | Sensitive | MFA | Member | Staff | Admin | Leader |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `giving:read_own` | | | ✓ | ✓ | ✓ | ✓ |
| `finance:read` | ✓ | | | ✓ | ✓ | ✓ |
| `finance:create` | | | | ✓ | ✓ | ✓ |
| `finance:update` | ✓ | | | ✓ | ✓ | ✓ |
| `finance:export` | ✓ | ✓ | | | ✓ | ✓ |

`giving:read_own` is not in the display catalogue (it's not a grantable
checkbox — see §3) but is enforced the same way: a member reads only their
own giving record, never another member's, regardless of role.

### Communications — announcements and message templates

| Permission | Sensitive | MFA | Member | Staff | Admin | Leader |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `announcements:read` | | | ✓ | ✓ | ✓ | ✓ |
| `announcements:create` | | | | ✓ | ✓ | ✓ |

### Administration — team, roles and organisation settings

| Permission | Sensitive | MFA | Member | Staff | Admin | Leader |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `team:read` | | | | | ✓ | ✓ |
| `team:invite` | ✓ | | | | ✓ | ✓ |
| `roles:read` | | | | | ✓ | ✓ |
| `roles:create` | ✓ | ✓ | | | ✓ | ✓ |
| `org:read` | | | | | ✓ | ✓ |
| `org:settings` | ✓ | ✓ | | | ✓ | ✓ |
| `billing:manage` | ✓ | | | | ✓ | ✓ |
| `org:documents` | ✓ | | | | ✓ | ✓ |
| `hierarchy:read` | | | | | ✓ | ✓ |
| `hierarchy:update` | | | | | ✓ | ✓ |
| `analytics:read` | | | | | ✓ | ✓ |
| `network:read` | | | | | ✓ | ✓ |
| `parish_comparison:read` | | | | | ✓ | ✓ |
| `domain:manage` | ✓ | ✓ | | | ✓ | ✓ |
| `white_label:manage` | ✓ | | | | ✓ | ✓ |
| `affiliations:read` | | | | | ✓ | ✓ |
| `affiliations:manage` | ✓ | ✓ | | | | **leader only** |
| `leadership:manage` | ✓ | | | | | **leader only** |
| `admins:manage` | ✓ | | | | | **leader only** |

`roles:read`, `org:read`, `hierarchy:read`, `hierarchy:update`,
`analytics:read`, `network:read`, `parish_comparison:read` are granted to
`board`/`leader` in code but aren't separate catalogue checkboxes for
custom roles — they ride along with `team:read`/`org:settings`-level access
in the UI. `leadership:manage`, `admins:manage`, `affiliations:manage` are
`LEADER_ONLY_PERMISSIONS`: never part of the administrator role, and — by
the no-escalation rule (`ensure_grantable`) — only grantable by someone who
already holds them, which in practice means only the leader.

### Always available (not in the catalogue — baseline, every authenticated member)

| Permission | Granted to |
|---|---|
| `portal:view` | member, staff, board, leader |
| `profile:read` | member, staff, board, leader |
| `profile:update_own` | member, staff, board, leader |
| `notifications:read` | member, staff, board, leader |
| `dashboard:view` | staff, board, leader |
| `certificates:read` | staff, board, leader |
| `certificates:create` | staff, board, leader |

## 3. How a permission is checked (RBAC) and how a record is checked (ABAC)

Two independent layers (`app/core/authz.py` docstring), always in this
order:

1. **RBAC — may this role do X at all?** `require_permission("x:y")`
   (`app/core/deps.py`) checks the permission list embedded in the access
   token, resolved from the membership's role at sign-in and re-validated
   against the database on every request (`_ensure_session_current` — a
   role change or suspension takes effect on the very next request, not
   when the token happens to expire). A handful of permissions
   additionally require `claims.mfa_verified` (the **MFA** column above).
   Platform admins bypass this check entirely (`claims.is_platform_admin`).
2. **ABAC — may this role do X to *this* record?** `app/core/authz.py`'s
   `Scope` compares the subject's `unit_scope_id` (and its descendants,
   one closure-table query) against the record's `unit_id`:
   - `members` — visible if their home unit, or a unit they are an active
     associate of, is in scope; writable only from their home unit.
   - `groups`, `events` — readable if church-wide (`unit_id IS NULL`) or in
     scope; writable only if in scope (a scoped session can never make or
     edit a church-wide record).
   - `pastoral_notes`, `sacramental_records`, `group_memberships`,
     `event_reviews`, `attendance_records` — inherit their parent record's
     visibility (a child row is visible/writable exactly when its parent
     member/group/event is).
   - **self** — a member-role session reaches only the member record linked
     to its own user id (`ensure_self_or_permission`).
   - Out-of-scope answers **404, not 403**: a scoped session learns nothing
     about whether a record outside its branch exists.
3. **The database enforces both independently.** RLS (`tenant_isolation` +
   RESTRICTIVE `unit_scope*` policies) applies regardless of what the
   application layer does or forgets — see
   [ARCHITECTURE.md](ARCHITECTURE.md) §3 and
   [THREAT_MODEL.md](THREAT_MODEL.md) §3.1–3.2 for why both layers exist on
   purpose.

## 4. Step-up (fresh-MFA) actions outside the permission system

Some actions are gated by `require_step_up` (a short-lived
`X-Step-Up-Token` proving MFA was *just* re-verified, independent of
`claims.mfa_verified` on the access token) rather than, or in addition to,
a permission:

| Action | Who | Why step-up, not just a permission |
|---|---|---|
| Request a leadership transfer | The current leader only | Changes who holds the highest-privilege role in the church |
| Approve a leadership transfer | Any other eligible active team member, two required | Separation of duties — no single approval moves the role |
| Cancel a leadership transfer | The outgoing leader only | Same channel as initiating it |
| Disable MFA | The account holder | Removing your own second factor should cost more than a password |

## 5. Known gaps

- **One role per login per church.** `tenant_memberships` is unique per
  `(user_id, tenant_id)` — a person can't hold two different scoped roles
  in the same church (e.g. whole-church accountant *and* branch-scoped
  youth leader) today. Tracked as the "next structural change" in
  [ARCHITECTURE.md](ARCHITECTURE.md) §6.
- **Giving has no ABAC scope.** `Fund`/`DonationBatch`/`Donation`/`Pledge`
  carry no `unit_id`; `finance:*` permissions are tenant-wide regardless of
  branch. See [THREAT_MODEL.md](THREAT_MODEL.md) §3.2.
- **This document is manual.** Nothing fails CI today if the code's
  `PERMISSION_CATALOGUE`/`SYSTEM_ROLE_PERMISSIONS` changes and this file
  isn't updated to match. Consider a test that fails when they diverge, the
  same way `tests/test_architecture.py` ratchets the layering rules.
