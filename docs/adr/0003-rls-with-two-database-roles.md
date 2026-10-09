# ADR-0003: Tenant isolation by RLS with two runtime database roles

- **Status:** Accepted
- **Date:** 2026-10-02 (recorded 2026-10-09)
- **Related:** TODO.md SA-03, SA-05; docs/ARCHITECTURE.md §2–3; docs/SECURITY_NOTES.md §1

## Context

A single forgotten `WHERE tenant_id = …` in application code would leak one
church's people to another. Application checks alone are one mistake away from
a breach.

## Decision

- The API connects as `app_tenant`. That role is not the table owner and has
  no `BYPASSRLS`, so every tenant table's `tenant_isolation` policy applies.
- The tenant comes only from the signed JWT. It is pinned per transaction
  with `set_config('app.tenant_id', …, true)`. Policies compare against
  `current_tenant_id()`, which returns NULL when the setting is unset or
  empty, so they fail closed.
- `app_platform` (`BYPASSRLS`) serves only platform-admin routes, behind
  `require_platform_admin`, with explicit audit entries.
- The owner role runs migrations and nothing else.

## Alternatives considered

- **Application-only filtering.** One bug leaks data.
- **A per-request role switch with `SET ROLE`.** Adds complexity for no gain
  over the transaction-local settings.

## Consequences

- Two independent layers have to fail before data leaks.
- Every new tenant table needs a policy. The migration convention and the PR
  checklist cover this.
- Cross-tenant operations need deliberate `SECURITY DEFINER` functions, which
  makes each crossing visible.

## Revisit when

Moving off PostgreSQL, or connection pooling makes transaction-local settings
unreliable. Pool in transaction mode only.
