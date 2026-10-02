# EcclesiaFlow API

The backend for the EcclesiaFlow frontend (`../ecclesia-flow-app`), built against
`docs/database-design.md` (in that repo) and `plan.md`'s architecture roadmap: FastAPI,
PostgreSQL via SQLAlchemy 2.0 async, strict multi-tenant isolation via Postgres
Row-Level Security, and `resource:action` RBAC + unit-scope ABAC.

## What's built so far

**Phase 0 — Base architecture.** Project layout (`app/modules/<name>/{models,schemas,repository,service,router}.py`
per module, per plan.md's "Strict Domain Scaffolding" guardrail), Pydantic settings,
async SQLAlchemy engine/session machinery, Alembic async migrations.

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
- Every table from `docs/database-design.md` exists as both a SQLAlchemy model and
  a migrated Postgres table, so later phases build on a complete schema rather than
  adding tables as they go.

**Security hardening applied across the board** (see `docs/SECURITY_NOTES.md` for
the two non-obvious bugs this surfaced): Postgres RLS on every tenant table (not
just the ones with a `NOT NULL tenant_id` — `roles`/`role_permissions` got an
explicit policy too, closing a gap the generic approach misses), two DB roles
(`app_tenant` RLS-bound, `app_platform` bypassrls for the platform-admin service,
not yet built), rate limiting on auth endpoints, security headers, request-id
correlation, and a single exception-handling path that never leaks a stack trace,
SQL fragment, or internal path to a client.

## Not built yet

Everything else in plan.md's roadmap: `hierarchy` (beyond the root unit created at
registration), full `rbac` CRUD (custom role builder), `people` (members/households
beyond the schema), `activity`, `finance`, `messaging`/`media`, `certificates`,
platform-admin, and the frontend-type endpoints those modules would expose. The
mock services in `ecclesia-flow-app/src/services/` still back the frontend for
all of that.

## Local setup

```bash
cp .env.example .env            # edit if you change ports/passwords
docker compose up -d            # Postgres on localhost:55433
python -m venv .venv && source .venv/Scripts/activate   # or .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
alembic upgrade head             # extensions -> schema -> RLS/seed/db-roles
uvicorn app.main:app --reload --port 8001
```

Open http://127.0.0.1:8001/docs for interactive OpenAPI docs (disabled automatically
when `ENVIRONMENT=production`).

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

Migrations run as the superuser (`ecclesia_admin` in `.env`'s `DATABASE_URL`) and
create two additional roles the running API actually connects as:

- `app_tenant` — every ordinary request. RLS-bound.
- `app_platform` — reserved for the platform-admin service (not yet built).
  Bypasses RLS; every connection on it is expected to pair with an explicit
  `audit_logs` write with `tenant_id = null`.

If you reset the database (`docker compose down -v`), migrations recreate both
roles from `APP_TENANT_DB_PASSWORD`/`APP_PLATFORM_DB_PASSWORD` in `.env`.
