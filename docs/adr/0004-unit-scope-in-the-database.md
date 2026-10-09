# ADR-0004: Branch (unit) scope enforced in the database for people data

- **Status:** Accepted
- **Date:** 2026-10-09
- **Related:** migration 20261009090000, tests/test_foundation.py, TODO.md SA-05

## Context

Inside one organisation, branch-scoped staff should see only their branch's
people. The application enforced this, but nothing in the database did.

## Decision

- Every tenant transaction also sets `app.unit_scope`: `'all'`, or the uuid
  of the scope root.
- RESTRICTIVE policies on `members`, `pastoral_notes`, `sacramental_records`
  and `unit_memberships` call `unit_in_session_scope()`. That function is
  PL/pgSQL so its cast can't be folded early by the planner.
- A person is readable from their home unit and from any unit they are an
  active associate of. Only the home unit can write to the record.
- When `app.unit_scope` is unset, no people are visible.

## Alternatives considered

- **Application checks only.** This was the state before.
- **A unit column on every table with policies everywhere at once.** Too
  broad a change in one step. Groups, events, attendance and giving still
  rely on the application, and are tracked in TODO.md.

## Consequences

- The scope can't be bypassed by a query that forgets the helpers.
- Church-wide uniqueness checks, such as envelope numbers, need
  `SECURITY DEFINER` helpers.
- Per-row function calls add a small cost to member lists.

## Revisit when

Several scoped roles per login are introduced (DA-04). The setting would then
carry a set of scopes per permission.
