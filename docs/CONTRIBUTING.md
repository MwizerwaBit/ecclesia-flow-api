# Contributing to the EcclesiaFlow API

For anyone changing this repository. Read alongside
[ARCHITECTURE.md](ARCHITECTURE.md) (how the system fits together) and
[adr/](adr/) (why it is the way it is).

## Project structure

```
app/core/           cross-cutting: config, database sessions, auth deps, authz, logging, limits
app/modules/<name>/ router.py → service.py → repository.py, schemas.py
prisma/             schema.prisma (columns) + migrations/ (hand-written SQL)
scripts/            operational scripts (seed, audit checkpoints)
tests/              pytest, against a real PostgreSQL
docs/               architecture, ADRs, standards, runbooks
```

How to add a module: ARCHITECTURE.md §5.

## Naming

| Thing | Convention | Example |
|---|---|---|
| Python modules, functions, variables | `snake_case` | `move_home`, `unit_scope_id` |
| Classes, Pydantic models | `PascalCase`; `…Read`, `…Create`, `…Update` for API shapes | `UnitTypeCreate` |
| Tables and columns | `snake_case`, plural tables | `unit_memberships.started_on` |
| Constraints, indexes | `<table>_<what>_<kind>` | `unit_memberships_one_home_idx` |
| Migrations | `YYYYMMDDHHMMSS_short_description` | `20261009120000_password_reset_and_audit_integrity` |
| Permissions | `resource:action` | `affiliations:manage` |
| Error codes | `snake_case`, stable once shipped | `reset_invalid`, `out_of_scope` |
| URL paths | plural nouns, kebab-case | `/unit-memberships/{id}/suspend` |
| Audit actions | `area.past_tense_verb` | `membership.home_moved` |

## Branching and commits

Three long-lived branches, promoted in one direction only:

```
feat/… fix/… chore/…  ──PR──▶  development  ──PR──▶  staging  ──PR──▶  master
      (short-lived)            (integration)        (release candidate,  (production;
                                                     deploys to staging)  tagged releases)
```

- **`development`** is where work lands. Branch from it and open your PR
  back into it.
- **`staging`** gets `development` when a set of changes is ready to try on
  the staging environment. Nothing is committed to it directly.
- **`master`** is production. It only receives `staging` once that set has
  passed on staging, and releases are tagged from it
  ([OPERATIONS.md](OPERATIONS.md#release)). All three branches are protected:
  no direct pushes, and CI must pass (FORYOU.md has the settings).
- **Hotfix:** branch `hotfix/…` from `master`, open a PR into `master`, then
  merge `master` back into `staging` and `development` so the fix isn't lost.
- Name working branches `<type>/<short-description>`, for example
  `feat/password-reset`, `fix/media-type-check` or `docs/adr-0008`.
- Write commit messages in the imperative ("Add password reset"), with a body
  explaining why when that isn't obvious. Reference the TODO.md id (`DIF-07`).
- Squash-merge working branches into `development` (one commit per change).
  Promotions (`development` → `staging` → `master`) use a **merge commit**,
  not a squash, so the branches keep a shared history and later promotions
  stay conflict-free.

## Pull requests and review

- Every change goes through a pull request. The
  [template](../.github/pull_request_template.md) is the checklist.
- At least **one approving review** from someone other than the author.
- Changes in these areas need a second reviewer who knows the security
  model: authentication, `app/core/authz.py`, `app/core/deps.py`, RLS
  policies or migrations, audit, or billing.
- Reviewers check behaviour, isolation (could another church or branch
  reach this?), tests that prove it, and migration safety
  ([MIGRATIONS.md](MIGRATIONS.md)).
- All required CI checks must pass. A red check is never merged "to fix
  later". Exceptions follow [SECURE_DEVELOPMENT.md](SECURE_DEVELOPMENT.md).

## Environment configuration

- All configuration is read by `app/core/config.py` (pydantic-settings) from
  environment variables or `.env`. Nothing else reads `os.environ`.
- `.env` is git-ignored. Copy `.env.example`, which lists every variable.
- A new setting needs three things: a field in `Settings` with a safe
  development default, an entry in `.env.example`, and, if production must
  override it, an entry in `Settings.production_problems()` so production
  refuses to start without it.
- Per-environment values and secret handling: [OPERATIONS.md](OPERATIONS.md).

## Local checks before pushing

```bash
python -m ruff check app tests scripts
python -m ruff format --check app tests scripts
python -m pytest -q
python -m bandit -r app scripts -ll -ii -q
```

CI runs the same commands, plus migrations against a clean database, a
dependency audit and a secret scan.

## Tests

- Test behaviour through the API where possible, as the existing tests do.
- Anything touching data access gets a **cross-tenant** attempt and, if
  branch scoping applies, a **cross-branch** attempt. Assert 404 rather than
  403 where the rule is "don't reveal existence".
- For RLS, also test straight against the database with
  `tenant_session(...)`, so the application checks aren't what's being
  tested.
- Tests run against a real PostgreSQL. Each test uses unique emails and
  church names rather than resetting data. CI uses a fresh database for every
  run.
