-- What the PREVIOUS release writes during the deploy window, after the migration has run.
INSERT INTO event_registrations (id, tenant_id, event_id, respondent_name, answers) VALUES
 ('b1000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-000000000001', 'Late visitor', '{"T-shirt": "L"}'),
 ('b1000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-000000000005', 'Late on an event with no form', '{"Brand new question": "x"}');
INSERT INTO event_registration_fields (id, tenant_id, event_id, label, field_type, options, required, position) VALUES
 ('a1000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-000000000005', 'Brand new question', 'text', '{}', false, 0);
-- the old release edits event 2's questions
DELETE FROM event_registration_fields WHERE event_id = 'e0000000-0000-0000-0000-000000000002';
INSERT INTO event_registration_fields (id, tenant_id, event_id, label, field_type, options, required, position) VALUES
 ('a1000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-000000000002', 'Replaced question', 'text', '{}', true, 0);
-- the NEW release already edited event 6's form, then the old one also changed its table: the new one must win
UPDATE forms SET updated_at = now() + interval '1 second' WHERE owner_id = 'e0000000-0000-0000-0000-000000000006';
DELETE FROM form_fields WHERE form_id = (SELECT id FROM forms WHERE owner_id = 'e0000000-0000-0000-0000-000000000006');
INSERT INTO form_fields (tenant_id, form_id, label, field_type, position)
SELECT tenant_id, id, 'Edited by new release', 'text', 0 FROM forms WHERE owner_id = 'e0000000-0000-0000-0000-000000000006';
DELETE FROM event_registration_fields WHERE event_id = 'e0000000-0000-0000-0000-000000000006';
