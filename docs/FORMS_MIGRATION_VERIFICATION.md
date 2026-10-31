# Forms module: migration verification report

- **Date:** 2026-11-01
- **Branch:** `feat/forms-module` (API and app)
- **Migrations:** `20261031090000_forms_module` (structure) and `20261031090100_forms_backfill` (data copy)
- **Aids:** `docs/migration-aids/forms-module-catch-up.sql`, `docs/migration-aids/forms-module-copy-back.sql`
- **Re-run it yourself:** `scripts/verify_forms_migration/run_all.ps1` (see its README)
- **Verdict:** safe to deploy by the normal release steps, with one extra step after the traffic switch (§6). Not
  tested against a copy of production data, which does not exist on this machine (§8).

## 1. What the migrations do

| | Migration 1: `forms_module` | Migration 2: `forms_backfill` |
|---|---|---|
| Changes | creates `forms`, `form_fields`, `form_submissions`; RLS and branch-scope policies; add-only grants and an update-refusing trigger on responses; composite `(form_id, tenant_id)` keys; seeds `forms:*` for staff, board and leader; comments the old tables as superseded | copies every event's questions and responses into the new tables, keeping ids; marks copied responses `reconstructed` |
| Touches existing tables | adds rows to `role_permissions`, `COMMENT ON` the two old tables, and foreign keys to `organizations` and `hierarchy_units` | reads `events` and the two old tables, writes only the new tables |
| Drops or rewrites anything | nothing | nothing |
| Safe to run twice | no (plain `CREATE TABLE`; Prisma runs it once) | **yes**: adds only what is missing |

The old tables `event_registration_fields` and `event_registrations` are left in place and unused. A later migration
drops them once this has been live for a release.

## 2. Findings that changed the migration

Reading the repo's own rules ([MIGRATIONS.md](MIGRATIONS.md), [OPERATIONS.md](OPERATIONS.md) §Release) and measuring
turned up three problems in the first version. All three are fixed, and the checks below are the proof.

| # | Finding | How it was found | Fix |
|---|---|---|---|
| 1 | **Write lock on `organizations` and `hierarchy_units` for the whole copy.** Adding a foreign key takes `SHARE ROW EXCLUSIVE` until the transaction ends; with the copy in the same transaction that is about 40 s at 200 000 responses. Church sign-ups, settings and hierarchy edits would have stalled. | measured with `pg_locks` just before commit (§5) | split into two migrations: structure (0.2 s) and copy (read-only on everything that exists) |
| 2 | **Deploy-window gap.** The release steps are "migrate, then switch traffic", so the previous release keeps writing the *old* tables between the two. Those rows would never reach the new tables, and an organiser would see registrations vanish. | reading the release procedure | the copy is idempotent, and the same statements are `forms-module-catch-up.sql`, to run once after the switch (§6) |
| 3 | **A question or response could, in principle, belong to a different church than its form.** The foreign key was on `form_id` alone; the checklist asks for a same-organisation guard where an FK alone would cross tenants. RLS already refused it, but only at the application role. | MIGRATIONS.md review checklist | composite `(form_id, tenant_id)` keys; check E11 shows both inserts refused |

Browser testing found three more problems, all in the app and unrelated to the schema: the Question input was crushed
to a few characters beside the Type select on desktop (the old registration editor had the same flaw), long unbroken
names ran off the card on a phone, and the public pages showed a generic "request payload is invalid" for a bad phone
number. All are fixed.

## 3. Checks, and results

Run on throwaway databases at the previous schema, plus the real dev database for §3.1. Every "expect 0 rows" list was
empty and every other expectation held.

### 3.1 Real data (the dev database)

The dev database held 41 legacy registrations when migration 1 was applied; nothing writes the old tables since, so they
can be compared with the copy directly.

| Check | Result |
|---|---|
| Legacy events with data / event forms for them | 41 / 41, each with one form, same church and branch |
| Legacy responses compared field by field (id, church, name, email, phone, answers, time) | 41 of 41 identical, 0 differences |
| Copied responses with a well-formed snapshot listing every answered label | 41 of 41 |
| Legacy questions | **0 rows in the dev data** (earlier tests had cleared them), so question copying is **not** exercised by real data: see 3.2 |
| Running migration 2 on this data through Prisma | counts unchanged before and after (141 forms / 218 questions / 201 responses), parity still 0 differences |

### 3.2 Awkward data (6 events, 2 churches, 1 branch)

| Case | Result |
|---|---|
| One form per event with data, none for an event with nothing; church and branch carried over | as expected (events 1, 2, 3, 4, 6) |
| Question and response counts per form | match the legacy rows |
| Options containing a comma and a quote | identical |
| Multi-line, unicode and quoted answer text; answer keys with dots and spaces | identical |
| Answers whose question was deleted long ago | kept; snapshot gets a `text` or `checkbox` entry from the answer's shape, position 1000 |
| Responses to an event whose questions were all cleared | kept; snapshot built from the answers |
| Two questions with the same wording in one event (legacy allowed it) | both copied, migration does not fail |
| Public keys | 5 distinct, 32 characters, hex |
| Updating a response, even as the table owner | refused by the trigger |
| A question or response for a different church than its form | refused (two foreign key errors) |

### 3.3 Empty database and structure

All migrations apply in order to an empty database, and the structure checks (S1 to S8) pass: three tables, trigger
enabled, row-level security on all three with the branch policies restrictive, **responses add-only for both app roles**
(SELECT and INSERT only), nine permission rows seeded, the nine indexes present, the composite keys in place, the old
tables commented as superseded. CI runs PostgreSQL 17 on an empty database; this ran on 18.6 (§8).

## 4. Volume

5 000 events, 15 000 questions, 200 000 responses (six answers each, one label with no question):

| Step | Duration |
|---|---|
| Migration 1 (structure) | 0.2 s |
| Migration 2 (copy) | 39.9 s (35 to 43 s over four runs) |
| Catch-up script when there is nothing to do | 4.0 s |

Parity at volume: 5 000 forms, 15 000 questions, 200 000 responses, every list of differences empty. A typical church
has far fewer registrations; the copy scales with responses, roughly 5 000 per second here.

## 5. Locks

Measured with `pg_locks` just before each migration commits, on tables that already exist:

| Migration | Locks held |
|---|---|
| 1 structure (about 0.2 s) | `organizations`, `hierarchy_units`: `SHARE ROW EXCLUSIVE` (blocks writes to them for those 0.2 s); old registration tables: `SHARE UPDATE EXCLUSIVE` (from the comment; blocks no reads or writes); `role_permissions`: `ROW EXCLUSIVE` |
| 2 copy (about 40 s at 200 000 responses) | `events`, old registration tables: `ACCESS SHARE` only. **Blocks no read or write on anything that exists.** |

## 6. Deploy procedure (adds one step to OPERATIONS.md §Release)

1. Back up production and confirm the backup.
2. `python -m prisma migrate deploy`: runs both migrations. The previous release keeps working: it ignores the new
   tables and keeps writing the old ones.
3. Switch traffic to the new release.
4. **Run `docs/migration-aids/forms-module-catch-up.sql`** as the database owner, once, now that nothing writes the old
   tables. It copies anything registered during steps 2 to 3. It never replaces the questions of a form the new
   release has edited and never deletes a response; running it again changes nothing.
5. Verify as usual, and open an event's responses page.

Skipping step 4 loses nothing permanently (the rows stay in the old tables and the script can run any time), but until
it runs, registrations made in that window are missing from the organiser's page.

**Tested** (part C): late responses for an event with a form, for an event that had no form, a question set replaced
by the old release in an untouched form (taken over), and a form the new release had already edited (left alone); then
the script run twice more with no change in any count.

## 7. Rolling back

The schema is not rolled back (this project's policy: forward-fix, MIGRATIONS.md); the previous release works against
the new schema. To roll the *application* back, redeploy the previous release and then run
`docs/migration-aids/forms-module-copy-back.sql`, which copies what was registered through the new release back into
the old tables so the old release shows it.

**Tested** (part D): new questions and a new response for an event, plus a standalone form with a response, then the
script: the event's questions and the new response appear in the old tables, event questions follow the form, the
standalone form's response is *not* copied, every pre-existing legacy response is byte-identical to before (hash
compared), nothing is dropped, and a second run changes nothing.

What the old release cannot show after a rollback: standalone forms and their responses (they stay in the new
tables), response snapshots, and a form's open or closed status (a closed event form reads as open).

## 8. What this does not prove

- **No copy of production data.** The dev database is small and has no legacy questions. Awkward and large data were
  synthesised. Run the migration on a restored production copy first, as OPERATIONS.md already requires, and read
  the §3.1 queries (`check1_parity.sql`) against it.
- **PostgreSQL 18.6 locally, 17 in CI.** Nothing here uses a version-specific feature, and CI applies the migrations
  to an empty 17.
- **The deploy window was simulated** by writing to the old tables by hand, not by running two real releases side by
  side.
- **The copied responses' snapshots are reconstructed**, as flagged in the data (`reconstructed: true`): for those, "the
  questions as they were" is the questions at the time of the copy.
- **The 40 s copy holds one transaction open.** It blocks nothing (§5), but a deploy tool with a short statement
  timeout would need it raised.
- The migration pair is **not** idempotent as a unit on a half-applied database; Prisma's own bookkeeping is what
  stops migration 1 running twice.
