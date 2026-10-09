# Architecture Decision Records

One file per significant decision: a choice that is expensive to reverse,
changes how modules relate, or affects security or data. Numbered in order and
never deleted. When a decision is replaced, mark the old record `Superseded by
ADR-NNNN` and write a new one.

Copy [`0000-template.md`](0000-template.md), take the next number, and link the
ADR from the pull request that implements it (the PR template asks).

| # | Decision | Status |
|---|---|---|
| [0001](0001-modular-monolith-shared-postgres.md) | Modular monolith on one shared PostgreSQL database | Accepted |
| [0002](0002-relation-free-prisma-hand-written-sql.md) | Relation-free Prisma schema; constraints, RLS and triggers in hand-written SQL | Accepted |
| [0003](0003-rls-with-two-database-roles.md) | Tenant isolation by RLS with two runtime database roles | Accepted |
| [0004](0004-unit-scope-in-the-database.md) | Branch (unit) scope enforced in the database for people data | Accepted |
| [0005](0005-persons-are-per-organisation.md) | A person record belongs to one organisation; no global person directory | Accepted |
| [0006](0006-consented-affiliations.md) | Parent organisations see only what the child grants, via definer functions | Accepted |
| [0007](0007-tamper-evident-audit-log.md) | Audit log as per-organisation hash chains with external signed checkpoints | Accepted |
| [0008](0008-pagination-in-headers.md) | Pagination metadata in headers; bodies stay arrays | Accepted |
