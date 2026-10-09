# ADR-0006: Parent organisations see only what the child grants, via definer functions

- **Status:** Accepted
- **Date:** 2026-10-09
- **Related:** migration 20261009090000, app/modules/affiliations, TODO.md SA-07

## Context

Independent parishes register first and may later join a diocese. The brief
says a new parent must not silently gain access to previously independent
data.

## Decision

- `organization_affiliations` needs both sides to agree. One side proposes
  and the other accepts.
- The parent must be verified, a church has one parent at a time, and loops
  are refused.
- The child controls `grants` (`aggregate_stats`, `published_events`) and can
  change them at any time.
- The parent never gets a tenant session on the child. It reads only through
  `affiliate_summary` and `affiliate_published_events`. These are
  `SECURITY DEFINER` functions that re-check the relationship, its status and
  the grant on every call, and return only counts or shared gatherings.

## Alternatives considered

- **Hierarchy implies access** (a parent unit inside one tenant). This breaks
  the "no implicit access" rule and forces previously independent tenants
  to merge.
- **Granting the parent a role in the child tenant.** Too coarse, and it
  mixes two organisations' staff lists.

## Consequences

- Each new kind of parent visibility needs a new grant and a new function.
  That is deliberate friction.
- Writing audit entries in both organisations' logs for one action isn't
  possible from one tenant session. Each side logs its own actions.

## Revisit when

Customers need parent access to specific records under policy. That would
need record-level grants and a legal review.
