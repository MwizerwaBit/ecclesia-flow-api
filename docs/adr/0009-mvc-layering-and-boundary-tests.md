# ADR-0009: MVC layering mapped onto FastAPI modules, enforced by boundary tests

- **Status:** Accepted
- **Date:** 2026-10-15
- **Related:** docs/ARCHITECTURE.md §1a, tests/test_architecture.py, ADR-0001

## Context

We were asked to separate Model, View and Controller clearly without
rebuilding the application. An audit on 2026-10-15 found that the backend
already follows a router → service → repository split in each module, but
with leaks:

- 2 routers queried the database directly.
- 3 routers called repositories directly, skipping the service.
- 18 non-repository files ran about 110 queries, and 3 modules had no
  repository at all.
- 6 modules imported other modules' repositories or routers.
- 1 service depended on FastAPI.
- About 12 routers held business logic.

## Decision

**Mapping.** The terms differ from classic MVC, and the folder names stay
as they are:

| MVC role | Here | Owns |
|---|---|---|
| View | the separate frontend (`ecclesia-flow-app`); on the server side, the JSON shape in `schemas.py` | presentation |
| Controller | `router.py` | HTTP in and out, input validation via schemas, permission and scope checks, calling the service, choosing the status code |
| Application / domain | `service.py` (plus helpers such as `lifecycle.py`, `recovery.py`) | business rules, workflows, invariants, audit entries |
| Model / persistence | `repository.py` + Prisma models + the database's own constraints, RLS and triggers | queries and persistence |
| DTOs | `schemas.py` | request and response contracts; never raw database rows with secret fields |
| Cross-cutting | `app/core` | sessions and RLS context, auth dependencies, authz policy, limits, logging |

**Dependency direction.** `router → service → repository → database`.
A module may use another module's **service** or **schemas**, never its
repository or router. `app/core` imports no module.

**Kept as is (deliberate deviations):**

- File names stay `router.py`, the FastAPI convention, rather than
  `controller.py`. The table above is the mapping, and renaming 20 files
  would add churn without changing behaviour.
- Authorization guards (`ensure_*`, `Scope` helpers) are called from routers.
  That is policy, not business logic, and it keeps "who may call this"
  visible at the endpoint.
- `app/core` (`deps.py`, `authz.py`, `permissions.py`, `database.py`) runs
  raw SQL. It is infrastructure: session freshness, RLS context, and
  reading the attributes that ABAC decides on. Moving those lookups into
  module repositories would make `core` depend on modules and invert the
  layering.
- No repository interfaces or ABCs. Each repository has exactly one
  implementation (Prisma). Plain module-level functions are the interface,
  and tests run against the real database.

**Enforcement.** `tests/test_architecture.py` parses every module file and
fails when:

- a non-repository file queries the database
- a router imports a repository
- a module imports another module's repository or router
- a service imports FastAPI
- `app/core` imports a module

Violations that existed when this ADR was written are listed in the test as
"known". The test fails both on a *new* violation and on a fixed violation
that is still listed, so the lists can only shrink.

## Alternatives considered

- **Rename everything to MVC vocabulary.** Superficial uniformity for a lot
  of churn, and the brief says not to do this.
- **A separate domain layer with entities and repository interfaces.** No
  current need. The business rules are thin enough to live in services,
  and the database enforces the invariants.
- **Enforcing boundaries by review alone.** That is how the leaks above
  happened.

## Consequences

- Layer rules are checked in CI on every PR, at no runtime cost.
- Modules without a repository gain one (leadership, billing, portal,
  audit). Services lose their direct database calls.
- Cross-module data needs go through the owning module's service. That can
  mean a small new service function where a repository import used to be.

## Revisit when

Business rules grow complex enough to need a domain model with its own tests
independent of the database, or a module is extracted into a service
(ADR-0001).
