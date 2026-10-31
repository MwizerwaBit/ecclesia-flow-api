# Verifying the forms migrations

Re-runs the checks behind [docs/FORMS_MIGRATION_VERIFICATION.md](../../docs/FORMS_MIGRATION_VERIFICATION.md)
on throwaway PostgreSQL databases (`forms_v_clean`, `forms_v_edge`, `forms_v_vol`, `forms_v_lock`), which it
creates and drops. It never touches the database in `.env` except to read the server address and role.

```powershell
# Windows PowerShell, with psql on PATH and the project's .venv in place
.\scripts\verify_forms_migration\run_all.ps1
```

The role in `DATABASE_URL` must be able to `CREATE DATABASE` and own what it creates (the dev `ecclesia` role
does). Takes about 5 minutes, most of it the 200 000-response volume run.

| Part | What it checks |
|---|---|
| A | every migration on an empty database; tables, RLS, policies, add-only grants, trigger, indexes, composite keys, permissions |
| B | awkward legacy data: commas and quotes in options, unicode, odd answer keys, an event in a branch, two churches, duplicate question wording, answers whose question is gone |
| C | the deploy window: the previous release keeps writing the old tables, then `docs/migration-aids/forms-module-catch-up.sql` runs (and again: nothing changes) |
| D | application rollback: `docs/migration-aids/forms-module-copy-back.sql` |
| E | 5 000 events, 15 000 questions, 200 000 responses: duration and parity |
| F | the locks each migration holds on tables that already exist |

Every list printed under a heading that says "expect 0 rows" must be empty. The `check*.sql` files print what they
expect next to each result; there is no pass/fail switch, a person reads it.
