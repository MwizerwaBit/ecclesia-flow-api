# EcclesiaFlow API — architecture

For engineers working on the backend. This explains how the code is laid out,
what happens to a request, how tenant and branch isolation work, how
organisations relate to each other, and how to add a module. It also
records which parts of the target architecture are deliberately not built yet.

Related docs: [adr/](adr/) (decisions) · [API_STANDARDS.md](API_STANDARDS.md) ·
[CONTRIBUTING.md](CONTRIBUTING.md) · [MIGRATIONS.md](MIGRATIONS.md) ·
[OPERATIONS.md](OPERATIONS.md) · [SECURE_DEVELOPMENT.md](SECURE_DEVELOPMENT.md) ·
[SECURITY_NOTES.md](SECURITY_NOTES.md)

The short version: **a modular monolith on one PostgreSQL database.**
Organisation structure, identity, membership, permissions and product modules
are separate concepts joined by explicit relationships. Isolation is enforced
twice, by the application and by Postgres Row-Level Security (RLS).

---

## 1. Layout

```
app/
  main.py            app factory: middleware, exception handlers, lifespan
  api.py             every router, grouped PUBLIC / ALWAYS_ON / BUSINESS
  core/              cross-cutting: config, database sessions, deps (authn,
                     RBAC), authz (ABAC scope), permissions, security, pii,
                     pagination, body_limit (413s), logging (JSON + scrubbing),
                     mailer, private_storage (byte-sniffed file types)
  modules/<name>/
    router.py        HTTP: validation, permission + scope checks
    service.py       use cases and business rules
    repository.py    queries (Prisma models or raw SQL)
    schemas.py       Pydantic request/response models
prisma/
  schema.prisma      columns only, relation-free (see README)
  migrations/        hand-written SQL: FKs, checks, RLS, triggers, functions
```

Dependencies point inward: router → service → repository. A module talks to
another module's data through that module's service or repository, never by
writing to its tables directly. The exception is read-only joins for display,
such as a unit name next to a member.

### Modules

| Module | Owns |
|---|---|
| `identity` | accounts, login, MFA, refresh tokens, invitations, self-registration |
| `tenant` | registration of a new organisation and its root unit; public directory |
| `org` | profile, lifecycle state, module entitlements, documents, onboarding, **terminology** |
| `billing` | plans, checkout, provider webhooks |
| `hierarchy` | units, the closure table, **unit types** and placement rules |
| `membership` | **unit memberships**: home unit and associate units, with history |
| `affiliations` | **organisation affiliations**: parent/child relationships between churches |
| `rbac` | roles, permissions, permission catalogue |
| `team` | staff memberships (who can sign in to the church, with what role and scope) |
| `leadership` | church-named leadership positions |
| `people` | person records, households, pastoral notes, sacramental records |
| `groups` | groups, group roles, rosters, invitations |
| `activity` | events, review workflow, attendance |
| `finance` | funds, donations, pledges |
| `messaging`, `media`, `certificates`, `portal`, `audit`, `platform_admin` | as named |

Module names in **bold** come from the organisational-foundation work
(migration `20261009090000`).

---

## 2. A request, end to end

1. **Authenticate.** `deps.get_current_claims` verifies the JWT. Tenant,
   role, permissions and `unit_scope_id` come only from the signed token,
   never from a header, query parameter or body.
2. **Open a scoped transaction.** `deps.get_tenant_db` calls
   `database.tenant_session(tenant_id, user_id, unit_scope_id)`. This opens a
   transaction on the `app_tenant` role (RLS applies, no BYPASSRLS) and pins
   three transaction-local settings:
   - `app.tenant_id`: the church
   - `app.unit_scope`: `'all'` for a whole-church session, or the uuid of the
     branch the session is limited to
   - `app.user_id`: the caller
3. **Check the session is current.** `_ensure_session_current` re-reads the
   membership. A suspended member, or a role or scope change, takes effect on
   the next request rather than when the 15-minute token expires.
4. **Check the organisation is active** (BUSINESS routers only).
5. **RBAC.** `require_permission("members:read")`, plus MFA for the
   permissions in `MFA_REQUIRED_PERMISSIONS`.
6. **ABAC.** `authz.Scope` and the `ensure_*` guards compare the session's
   unit scope with the record's unit. Records out of scope return 404, so
   their existence isn't revealed.
7. **The service runs the use case** inside the same transaction, and writes
   `audit_logs` for sensitive changes.
8. **Postgres enforces RLS** on every query regardless of steps 5–7.

Anything that sets `app.tenant_id` outside `tenant_session` (registration,
self-join, invitation acceptance, public event pages) must go through
`database.set_tenant_context`, which also sets `app.unit_scope`.

---

## 3. Isolation model

There are two boundaries, and each is enforced by both layers.

| Boundary | Application | Database |
|---|---|---|
| **Tenant**: church A never sees church B | tenant comes only from the JWT | `tenant_isolation` policy (`tenant_id = current_tenant_id()`) on every tenant table |
| **Unit**: branch staff see only their branch's people | `authz.Scope`: `unit_visible`, `member_visible`, `ensure_member_*` | `unit_scope*` RESTRICTIVE policies on `members`, `pastoral_notes`, `sacramental_records`, `unit_memberships` |

Rules worth knowing before you touch RLS:

- **Fail closed.** `current_tenant_id()` and `current_unit_scope()` return NULL
  when their setting is unset or empty. Every policy then matches nothing.
  A transaction that sets the tenant but forgets the unit scope sees the
  church's structure but none of its people (tested in
  `test_foundation.py::test_database_enforces_unit_scope_without_the_application`).
- **Unit-scope policies are RESTRICTIVE.** They are ANDed with the permissive
  tenant policy, so they can only narrow what a session sees.
- **Updates check both sides.** `USING` checks the row as it is and
  `WITH CHECK` checks the row as it would become, so a record can't be moved
  out of the session's scope.
- **`unit_in_session_scope()` is PL/pgSQL on purpose.** If written as an
  inlined SQL function, the planner may cast `'all'` to uuid and fail, even
  inside a CASE branch that never runs.
- **Cross-boundary reads go through `SECURITY DEFINER` functions** that return
  the minimum needed. Examples: `tenant_envelope_taken` and
  `tenant_next_envelope_number` (church-wide uniqueness for a branch
  session), `affiliate_summary` and `affiliate_published_events` (what a
  parent may see), and `verify_certificate_by_hash`. Each one revokes
  `PUBLIC` and grants only the app roles.
- **`app_platform` bypasses RLS.** Only `require_platform_admin` routes use it,
  and they write an audit entry explicitly.

Who sees a person: their **home unit's** scope, plus the scope of any unit
they are an **active associate** of. Only the home unit can edit them.

---

## 4. Organisation model

```
Organization  (tenant: the customer, billing, configuration)
 ├── HierarchyUnit tree  (closure table; typed by UnitType, optional)
 │     └── Member  ── home UnitMembership (exactly one current) + associate UnitMemberships
 ├── OrganizationConfiguration  (terminology)
 └── OrganizationAffiliation ──► another Organization (parent), by mutual consent
```

### Units and unit types

- `hierarchy_units` is a tree within one organisation. Triggers enforce
  three things: the parent is in the same organisation (FK checks ignore
  RLS, so the FK alone isn't enough), the tree has no cycles, and the
  closure table follows every move. A moved branch takes its scope with it.
- `unit_types` are a church's own names for its levels and say which level may
  sit under which. They are opt-in. Once a church defines any, new and
  re-typed units must use one. Presets (`single_church`, `diocesan`,
  `multi_campus`, `association`) are only starting points. **No code
  branches on a unit's type.** A "Parish" is configuration, not a class.

### Unit memberships

`members.unit_id` is still the home unit that every module filters on.
`unit_memberships` holds the history: `home` or `associate` kind, status
`active`, `suspended`, `transferred` or `ended`, and dates and reasons. The
`members_sync_home_unit` trigger records history on every write path, even
ones that only set `members.unit_id`. `POST /members/{id}/move` records the
move with a reason. A cross-church transfer (`execute_member_transfer`)
closes the person's memberships as `transferred`.

### Affiliations

An independently registered church can later join a parent such as a diocese
or a denomination:

1. Either side proposes (`POST /affiliations`); the other side must accept.
2. The parent must be `verified` (the platform verifies its documents).
3. A church has at most one open parent relationship, and the chain can't
   loop (`affiliation_would_cycle`).
4. **The child decides what the parent sees** (`grants`: `aggregate_stats`,
   `published_events`) and can change it at any time.
5. The parent never gets a tenant session on the child. It reads only
   through the two `affiliate_*` functions, which re-check the relationship,
   its status and the grant on every call. Becoming a parent grants
   **no** access to people, notes, giving or anything else.
6. Either side can end the relationship. Rows are never deleted, and each
   side's actions go to its own audit log.

### Terminology

`GET/PUT /org/terminology` changes labels only, such as "Parish" or "Parish
secretary". Permissions belong to roles, which are keyed by id, so renaming
never changes what anyone can do.

---

## 5. Adding a module

1. Create `app/modules/<name>/` with `schemas.py`, `repository.py`,
   `service.py` and `router.py`.
2. Write a hand-written migration. Every table holding a church's data needs:
   - a `NOT NULL tenant_id` with an FK to `organizations`
   - `ENABLE ROW LEVEL SECURITY` and a `tenant_isolation` policy
   - a RESTRICTIVE `unit_scope` policy if the rows are people data or
     hang off a person
   - grants for `app_tenant` and `app_platform`
   Add the columns to `schema.prisma`, then run `prisma migrate deploy` and
   `prisma generate`.
3. Add permissions to `rbac/models.py` (`SYSTEM_ROLE_PERMISSIONS`,
   `PERMISSION_CATALOGUE`, and `MFA_REQUIRED_PERMISSIONS` if sensitive).
   Insert them into `role_permissions` in the migration.
4. Register the router in `app/api.py` in the right group. Group membership
   is a security decision.
5. Write tests: the happy path, a cross-tenant attempt, and a branch-scoped
   attempt. Where RLS is involved, also test directly against the database
   with `tenant_session(...)`.

---

## 6. Against the target architecture

The product architecture brief asks for a lot. Here is where each part stands.

| Area | State |
|---|---|
| Modular monolith, one Postgres, module boundaries | Built |
| Tenant isolation, app + RLS, non-owner runtime role, fail-closed context | Built |
| Unit-level isolation in the database | Built for people data. Groups, events, attendance and giving are still unit-scoped by the application only |
| Configurable hierarchy, unit types, cycle-safe moves | Built |
| Later affiliation with governed, consented parent visibility | Built (counts and shared gatherings) |
| Membership lifecycle, history, several units per person | Built within one church |
| Configurable terminology | Built (labels) |
| MFA, step-up, session freshness, audit log, rate limiting | Built |
| **One login holding several roles in one church** (e.g. accountant at parish level *and* priest at one church) | **Not built.** A login has one role and one scope per church (`tenant_memberships` is unique per user and tenant). Needs per-assignment scopes in the token and in `Scope`. This is the next structural change. |
| Person ↔ login link as its own entity (`AccountPersonLink`) | Not built. `members.user_id` (unique per church) is the link, and a person exists without a login. Persons are per church on purpose: no global person directory |
| Family relationships (parent, guardian, spouse) with their own authorization | Not built. Households exist |
| Custom fields and form definitions | Not built |
| Parent-level access beyond aggregates (e.g. shared reports, specific records under policy) | Not built. Would be new grants backed by new `SECURITY DEFINER` functions, never a tenant session |
| Tamper-evident audit log, retention policies | Not built. `audit_logs` is append-only by convention |
