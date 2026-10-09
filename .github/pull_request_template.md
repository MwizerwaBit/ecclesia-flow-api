## What and why

<!-- One or two sentences. Link the TODO.md item (e.g. DIF-07) and any ADR. -->

## Checks

- [ ] Tests added or updated for the change (including a cross-tenant / cross-branch attempt if it touches data access)
- [ ] New tenant tables: `tenant_id`, RLS `tenant_isolation`, a unit-scope policy if it holds people data, grants (docs/ARCHITECTURE.md §5)
- [ ] Migration reviewed against docs/MIGRATIONS.md (forward-only, tested on a clean database)
- [ ] No secrets, tokens or personal data in code, logs or fixtures
- [ ] Significant architectural decision recorded as an ADR (docs/adr/)
- [ ] TODO.md updated
