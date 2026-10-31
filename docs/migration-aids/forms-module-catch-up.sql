-- Forms module: catch up registrations written to the old tables during the deploy window.
--
-- Why: migration 20261031090100_forms_backfill copies event_registration_fields / event_registrations into the
-- forms tables, and the release procedure is "migrate, then switch traffic" (OPERATIONS.md). Between
-- the two the previous release is still serving and still writes the OLD tables, so anything it
-- saves in that window is not in the new ones.
--
-- When: once, right after traffic has moved to the new release (so nothing writes the old tables
-- any more). As the database owner, in the production database:
--     psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f docs/migration-aids/forms-module-catch-up.sql
--
-- Safe to run again: it only adds what is missing, and it only replaces the questions of a form
-- that the new release has never edited (updated_at = created_at), so it can't undo a change made
-- through the new release. It never deletes a response.

BEGIN;

-- 1. Events that got questions or responses in the window and so have no form yet.
INSERT INTO forms (id, tenant_id, unit_id, owner_kind, owner_id, title, status, public_key, created_by_user_id, published_at)
SELECT gen_random_uuid(), e.tenant_id, e.unit_id, 'event', e.id, e.title, 'published',
       replace(gen_random_uuid()::text, '-', ''), e.created_by_user_id, CURRENT_TIMESTAMP
FROM events e
WHERE (EXISTS (SELECT 1 FROM event_registration_fields f WHERE f.event_id = e.id)
    OR EXISTS (SELECT 1 FROM event_registrations r WHERE r.event_id = e.id))
  AND NOT EXISTS (SELECT 1 FROM forms fm WHERE fm.owner_kind = 'event' AND fm.owner_id = e.id);

-- 2. Questions: for forms the new release has not touched, make them match the old table.
CREATE TEMP TABLE _forms_changed AS
SELECT fm.id AS form_id, fm.owner_id AS event_id
FROM forms fm
WHERE fm.owner_kind = 'event' AND fm.updated_at = fm.created_at
  AND EXISTS (SELECT 1 FROM event_registration_fields f WHERE f.event_id = fm.owner_id)
  AND (SELECT coalesce(jsonb_agg(jsonb_build_array(f.label, f.field_type, to_jsonb(f.options), f.required, f.position)
                                 ORDER BY f.position, f.id), '[]'::jsonb)
         FROM event_registration_fields f WHERE f.event_id = fm.owner_id)
   <> (SELECT coalesce(jsonb_agg(jsonb_build_array(x.label, x.field_type, to_jsonb(x.options), x.required, x.position)
                                 ORDER BY x.position, x.id), '[]'::jsonb)
         FROM form_fields x WHERE x.form_id = fm.id);

DELETE FROM form_fields WHERE form_id IN (SELECT form_id FROM _forms_changed);
INSERT INTO form_fields (id, tenant_id, form_id, label, field_type, options, required, position, created_at)
SELECT f.id, f.tenant_id, c.form_id, f.label, f.field_type, f.options, f.required, f.position, f.created_at
FROM _forms_changed c JOIN event_registration_fields f ON f.event_id = c.event_id
ON CONFLICT (id) DO NOTHING;

-- 3. Responses the new tables don't have, marked reconstructed exactly as the migration marks them.
INSERT INTO form_submissions (id, tenant_id, form_id, respondent_name, respondent_email, respondent_phone,
                              answers, form_snapshot, created_at)
SELECT r.id, r.tenant_id, fm.id, r.respondent_name, r.respondent_email, r.respondent_phone, r.answers,
       jsonb_build_object(
         'form_title', fm.title,
         'reconstructed', true,
         'fields',
           (SELECT coalesce(jsonb_agg(jsonb_build_object(
                      'label', f.label, 'field_type', f.field_type, 'options', to_jsonb(f.options),
                      'required', f.required, 'position', f.position) ORDER BY f.position), '[]'::jsonb)
              FROM form_fields f WHERE f.form_id = fm.id)
           || (SELECT coalesce(jsonb_agg(jsonb_build_object(
                      'label', a.key,
                      'field_type', CASE WHEN jsonb_typeof(a.value) = 'array' THEN 'checkbox' ELSE 'text' END,
                      'options', '[]'::jsonb, 'required', false, 'position', 1000)), '[]'::jsonb)
                 FROM jsonb_each(r.answers) AS a(key, value)
                WHERE NOT EXISTS (SELECT 1 FROM form_fields f2 WHERE f2.form_id = fm.id AND f2.label = a.key))
       ),
       r.created_at
FROM event_registrations r
JOIN forms fm ON fm.owner_kind = 'event' AND fm.owner_id = r.event_id AND fm.tenant_id = r.tenant_id
WHERE NOT EXISTS (SELECT 1 FROM form_submissions s WHERE s.id = r.id);

DROP TABLE _forms_changed;

COMMIT;
