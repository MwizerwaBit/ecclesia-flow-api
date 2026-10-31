-- Forms module: copy event registration data back, for rolling the APPLICATION back a release.
--
-- Why: after the forms release, an event's questions and responses live in the forms tables. The
-- previous release reads the old tables (event_registration_fields / event_registrations), which
-- the migration left in place. Redeploying the previous release without this shows the state as it
-- was at migration time: anything registered since is missing from its screens.
--
-- When: after the previous release is serving, and before anyone relies on it. Per MIGRATIONS.md
-- the schema is NOT rolled back (no tables are dropped here): the new tables stay, unused, and a
-- later roll-forward needs only forms-module-catch-up.sql. Run as the database owner:
--     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f docs/migration-aids/forms-module-copy-back.sql
--
-- Safe to run again. What is NOT carried back, because the old release has nowhere to put it:
-- standalone forms and their responses (they stay in the forms tables), response snapshots, and
-- form status (a closed event form reads as open to the old release).

BEGIN;

-- Questions: the old table becomes what the new one holds, for every event that has a form.
DELETE FROM event_registration_fields
WHERE event_id IN (SELECT owner_id FROM forms WHERE owner_kind = 'event');

INSERT INTO event_registration_fields (id, tenant_id, event_id, label, field_type, options, required, position, created_at)
SELECT ff.id, ff.tenant_id, f.owner_id, ff.label, ff.field_type, ff.options, ff.required, ff.position, ff.created_at
FROM form_fields ff
JOIN forms f ON f.id = ff.form_id AND f.tenant_id = ff.tenant_id
WHERE f.owner_kind = 'event' AND EXISTS (SELECT 1 FROM events e WHERE e.id = f.owner_id);

-- Responses: add the ones the old table lacks. Existing rows are never touched.
INSERT INTO event_registrations (id, tenant_id, event_id, respondent_name, respondent_email, respondent_phone, answers, created_at)
SELECT s.id, s.tenant_id, f.owner_id, s.respondent_name, s.respondent_email, s.respondent_phone, s.answers, s.created_at
FROM form_submissions s
JOIN forms f ON f.id = s.form_id AND f.tenant_id = s.tenant_id
WHERE f.owner_kind = 'event' AND EXISTS (SELECT 1 FROM events e WHERE e.id = f.owner_id)
ON CONFLICT (id) DO NOTHING;

COMMIT;
