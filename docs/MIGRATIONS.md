# Database migrations

How schema changes are written, reviewed, tested and recovered from. Why
migrations are hand-written: [ADR-0002](adr/0002-relation-free-prisma-hand-written-sql.md).

## Writing one

1. Create `prisma/migrations/<YYYYMMDDHHMMSS>_<description>/migration.sql` by
   hand.
2. Add or adjust the columns in `prisma/schema.prisma`. Don't declare
   relations.
3. Apply and regenerate the client locally:
   ```bash
   python -m prisma migrate deploy
   python -m prisma generate
   ```
   Don't use `prisma migrate dev`. Its generated diff drops hand-written
   objects (SECURITY_NOTES.md §6).
4. Never edit a migration after it has been merged, or applied anywhere but
   your own machine. Write a new one instead.

## Review checklist

Every migration PR is checked for the following. It's also in the PR template.

- [ ] **Tenant tables:** `tenant_id NOT NULL` with an FK to `organizations`;
  `ENABLE ROW LEVEL SECURITY`; a `tenant_isolation` policy; a RESTRICTIVE
  unit-scope policy if the table holds people data or rows that hang off a
  person; grants for `app_tenant` and `app_platform`.
- [ ] **Cross-boundary functions** are `SECURITY DEFINER` with
  `SET search_path = public`, `REVOKE ALL … FROM PUBLIC`, explicit grants, and
  they return the minimum needed.
- [ ] **Constraints** express the invariants: checks, partial unique indexes,
  same-organisation guards where an FK alone would cross tenants.
- [ ] **Indexes** exist for every new foreign key and query filter.
- [ ] **Locking:** long table rewrites or `ACCESS EXCLUSIVE` locks on big tables
  are flagged, with a plan (batches, `CREATE INDEX CONCURRENTLY` in its own
  migration, off-peak).
- [ ] **Backfills** are idempotent and bounded.
- [ ] **Application compatibility:** the previous release keeps working against
  the new schema. Expand first, contract in a later release (below).
- [ ] **Audit log:** never `UPDATE` or `DELETE` it. It's append-only
  ([ADR-0007](adr/0007-tamper-evident-audit-log.md)).

## Testing

- **CI** applies every migration in order to an empty PostgreSQL and runs the
  full test suite against it (`.github/workflows/ci.yml`, job `test`). A
  migration that only works on a database that already exists fails here.
- **Locally**, the same check can be run against a throwaway database. Point
  `DATABASE_URL*` at a fresh database, then run `prisma migrate deploy` and
  `pytest`. This was last verified on 2026-10-09: 12 migrations and 47 tests
  passed, before observability was added.
- Before a production release, apply the migration to a restored copy of
  production (OPERATIONS.md §Release) to measure duration and lock impact.

## Recovery strategy: forward-fix, with expand/contract

PostgreSQL DDL is transactional, so a migration that fails partway leaves
nothing behind: `migrate deploy` stops and the database is unchanged. The
real risk is a migration that *succeeds* but is wrong.

- **Default: forward-fix.** Write a new migration that corrects the problem.
  Down-migrations aren't kept, because a rollback script that has never run
  against production data is less safe than a reviewed forward fix.
- **Expand/contract** makes rollback unnecessary for most changes. Release
  N adds the new structure while keeping the old (new column, both written).
  Release N+1 switches reads. Release N+2 removes the old structure. At every
  step the previous application release still works, so rolling back the
  *application* never needs a schema rollback.
- **Destructive changes** (drop column or table, narrowing a type) only happen
  in a contract step, after a backup has been verified, and never in the same
  release as the code change that stops using the structure.
- **Last resort:** restore from backup to a point in time before the
  migration (OPERATIONS.md §Backups). Data written since then has to be
  reconciled by hand. This is why destructive steps are isolated.

## Editing an applied migration (local only)

If an unshared migration has to change while you're developing, edit the
file, apply the changed objects manually, and update
`_prisma_migrations.checksum` to the file's SHA-256. This was done once for
`20261009090000` before it was shared. Never do it for a migration anyone
else has applied.
