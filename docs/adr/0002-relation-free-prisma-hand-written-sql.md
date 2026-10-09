# ADR-0002: Relation-free Prisma schema; constraints, RLS and triggers in hand-written SQL

- **Status:** Accepted
- **Date:** 2026-10-02 (recorded 2026-10-09)
- **Related:** README "Why Prisma", docs/SECURITY_NOTES.md §6, docs/MIGRATIONS.md

## Context

Prisma's schema language can't express Row-Level Security, triggers, partial
indexes or `SECURITY DEFINER` functions. Declaring Prisma relations would add
a back-reference field to the referenced model for every table that points at
it. `Organization` alone would need about 15.

## Decision

`schema.prisma` lists columns only, with foreign keys as plain scalar columns.
Each migration is written by hand: tables, foreign keys, checks, indexes,
policies, triggers and functions. Prisma's generated diff is discarded,
because it tries to drop every hand-written object.

## Alternatives considered

- **Prisma relations and generated migrations.** Can't express the security
  model.
- **SQLAlchemy and Alembic.** This was the original stack. It was replaced by
  Prisma for the typed client, and the hand-written-SQL approach fits both.

## Consequences

- The database is the source of truth for integrity and security, which
  reviewers can read in one place.
- The schema file and the migrations can drift apart. CI applies every
  migration to a clean database and runs the tests (DIF-04) to catch this.
- `@default(uuid())` is client-side. Raw inserts must supply ids themselves
  (`gen_random_uuid()`).

## Revisit when

Prisma supports RLS and triggers natively, or the client is replaced.
