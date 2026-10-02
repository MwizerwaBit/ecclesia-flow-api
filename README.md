# EcclesiaFlow API

The backend for the EcclesiaFlow frontend (`../ecclesia-flow-app`), built against
`docs/database-design.md` (in that repo) and `plan.md`'s architecture roadmap: FastAPI,
PostgreSQL via Prisma (`prisma-client-py`), strict multi-tenant isolation via Postgres
Row-Level Security, and `resource:action` RBAC + unit-scope ABAC.

## What's built so far

**Phase 0 — Base architecture.** Project layout (`app/modules/<name>/{schemas,repository,service,router}.py`
per module, per plan.md's "Strict Domain Scaffolding" guardrail — `models.py` is
Prisma-generated now, not hand-written per module, see below), Pydantic settings,
Prisma schema + migrations.

**Phase 1 — `identity` & `tenant`**, fully implemented and integration-tested:

- Church registration (`POST /auth/register`) — creates the organization, its root
  hierarchy unit, the leader's user account, and a `tenant_memberships` row with
  `is_primary=true, is_leader=true`, all in one transaction.
- Login (`POST /auth/login`) with argon2 password hashing, constant-shape failure
  (wrong password and unknown email return the identical error).
- JWT access tokens (15 min default) carrying the resolved role, permissions, and
  ABAC `unit_scope_id` — never looked up again mid-request.
- Refresh tokens: opaque, stored only as a SHA-256 hash, **rotated on every use**.
  Reusing an already-rotated token is treated as theft and burns the entire
  rotation chain, not just the replayed token.
- MFA: TOTP enrollment (`/auth/mfa/setup` → `/auth/mfa/verify`), single-use backup
  codes (consumed on verification, not just checked), and a step-up flow
  (`/auth/step-up`) that gates sensitive actions (e.g. `/auth/mfa/disable`) behind
  a fresh MFA check via an `X-Step-Up-Token` header, independent of the normal
  15-minute access token.
- `GET /churches`, `GET /churches/{slug}` — the public, unauthenticated church
  directory (suspended/canceled orgs 404 like they don't exist).
- Every table from `docs/database-design.md` exists in `prisma/schema.prisma` and
  as a migrated Postgres table, so later phases build on a complete schema rather
  than adding tables as they go.

**Security hardening applied across the board** (see `docs/SECURITY_NOTES.md` for
the real bugs this surfaced, including two specific to the Prisma rewrite): Postgres
RLS on every tenant table (not just the ones with a `NOT NULL tenant_id` —
`roles`/`role_permissions` got an explicit policy too, closing a gap the generic
approach misses), two DB roles (`app_tenant` RLS-bound, `app_platform` bypassrls for
the platform-admin service, not yet built), rate limiting on auth endpoints, security
headers, request-id correlation, and a single exception-handling path that never
leaks a stack trace, SQL fragment, or internal path to a client.

## Not built yet

Everything else in plan.md's roadmap: `hierarchy` (beyond the root unit created at
registration), full `rbac` CRUD (custom role builder), `people` (members/households
beyond the schema), `activity`, `finance`, `messaging`/`media`, `certificates`,
platform-admin, and the frontend-type endpoints those modules would expose. The
mock services in `ecclesia-flow-app/src/services/` still back the frontend for
all of that.

## Why Prisma, and how the schema is organized

Prisma doesn't support Row-Level Security, Postgres functions/triggers, or partial
indexes in `schema.prisma` directly, so the split is:

- `prisma/schema.prisma` — every table's columns and basic constraints (uniques,
  composite PKs). **Deliberately relation-free**: a foreign key is a plain scalar
  column (`tenant_id String @db.Uuid`), never a Prisma `@relation`. Declaring one
  would force a back-reference array field onto the referenced model for every
  table that points at it — `Organization` alone would need ~15 — and the app
  never traverses these via Prisma's `include`, it always queries the child table
  directly, filtered by `tenant_id` (the same shape RLS itself enforces).
- `prisma/migrations/20261002090126_init/migration.sql` — the actual foreign key
  `CONSTRAINT`s, check constraints, partial/composite indexes, RLS policies, the
  closure-table trigger, the `execute_member_transfer` function, system-role seed
  data, and the two database roles. Prisma generated the `CREATE TABLE` statements
  at the top; everything below the `══...══` divider comment is hand-written.

Generated model classes (`prisma.models.User`, `.Organization`, etc.) and the
client (`from prisma import Prisma`) come from running `prisma generate` — there's
no equivalent of SQLAlchemy's hand-written model files to keep in sync.

## Local setup

This assumes a native PostgreSQL install already running on `localhost:5432` (not
Docker) with a login role that has `CREATEDB`:

```bash
# One-time: create the database (adjust user/password to match your install)
psql -U <your_pg_user> -d postgres -c "create database ecclesia_flow"

cp .env.example .env            # edit DATABASE_URL etc. to match your install
python -m venv .venv && source .venv/Scripts/activate   # or .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
python -m prisma migrate deploy  # applies prisma/migrations/ in order
python -m prisma generate        # regenerates the client (migrate deploy does NOT do this)
uvicorn app.main:app --reload --port 8001
```

Open http://127.0.0.1:8001/docs for interactive OpenAPI docs (disabled automatically
when `ENVIRONMENT=production`).

**A note on `prisma migrate dev`:** it works, but in a non-interactive shell it can
hang waiting for a migration-name prompt after applying — use `migrate deploy` (just
applies pending migrations, no prompts) day-to-day, and only reach for `migrate dev`
interactively when you're actually adding a new migration.

### Running tests

```bash
python -m pytest tests/ -v
```

Tests run against the same dev Postgres as above (each test uses a unique
email/church name rather than an isolated schema — a pragmatic choice for this
pass; see the docstring in `tests/test_auth_flow.py`). They cover the security
properties above end-to-end: weak-password rejection, duplicate-email rejection,
login failure-shape, refresh rotation + theft detection, MFA setup/verify/challenge
+ backup-code single-use, step-up gating, and basic tenant isolation.

### Database roles

The migration creates two roles the running API actually connects as (the
`DATABASE_URL` owner account — e.g. `ecclesiaDb` — is for migrations only, never
for request-serving traffic):

- `app_tenant` — every ordinary request. RLS-bound.
- `app_platform` — reserved for the platform-admin service (not yet built).
  Bypasses RLS; every connection on it is expected to pair with an explicit
  `audit_logs` write with `tenant_id = null`.

Their passwords are set from the migration SQL (`change_me_tenant_pw` /
`change_me_platform_pw` by default) and must match `APP_TENANT_DB_PASSWORD` /
`APP_PLATFORM_DB_PASSWORD` in `.env`. If you change them, update both the running
database (`ALTER ROLE app_tenant PASSWORD '...'`) and `.env` together.
