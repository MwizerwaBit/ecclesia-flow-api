# ADR-0001: Modular monolith on one shared PostgreSQL database

- **Status:** Accepted
- **Date:** 2026-10-02 (recorded 2026-10-09)
- **Related:** TODO.md DA-10, docs/ARCHITECTURE.md §1

## Context

A small team is building a multi-tenant church-management SaaS. Organisations
range from one congregation to a diocese. Isolation between tenants is the top
non-functional requirement. Traffic is modest and spiky: Sunday mornings, and
month-end giving reports.

## Decision

One deployable FastAPI application, split into modules under `app/modules/`.
Each module has its own router, service, repository and schemas, and
dependencies point inward. All tenants share one PostgreSQL database. Every
tenant table carries `tenant_id` and is protected by Row-Level Security
(ADR-0003).

## Alternatives considered

- **Microservices.** Every isolation rule would need re-implementing across
  network boundaries, and distributed transactions would be needed for flows
  such as registration and transfers. Too much operational cost for the team
  size.
- **Database or schema per tenant.** Gives strong isolation, but migrations
  multiply per tenant, cross-tenant operations (transfers, affiliations,
  platform administration) get hard, and connection counts grow with tenants.

## Consequences

- One transaction can span modules, so multi-step use cases stay atomic.
- Module boundaries are a convention, not enforced by the network. Reviews,
  and later automated boundary tests (DA-14), keep them honest.
- One noisy tenant can affect others' performance; indexes and pagination
  matter.

## Revisit when

A module needs to scale or deploy independently, shown by measurements; a
customer contractually requires physical separation; or the database becomes
the bottleneck after query and index work.
