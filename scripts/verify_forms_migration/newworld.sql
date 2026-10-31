-- What the NEW release does afterwards: a response, a new question, and a standalone form with a response.
INSERT INTO form_fields (tenant_id, form_id, label, field_type, position)
SELECT tenant_id, id, 'New world question', 'text', 3 FROM forms WHERE owner_id = 'e0000000-0000-0000-0000-000000000001';
UPDATE forms SET updated_at = now() + interval '2 seconds' WHERE owner_id = 'e0000000-0000-0000-0000-000000000001';
INSERT INTO form_submissions (id, tenant_id, form_id, respondent_name, answers, form_snapshot)
SELECT 'c1000000-0000-0000-0000-000000000001', tenant_id, id, 'New world person', '{"New world question": "hi"}', '{"form_title":"x","fields":[]}'
FROM forms WHERE owner_id = 'e0000000-0000-0000-0000-000000000001';
INSERT INTO forms (id, tenant_id, title, status, public_key) VALUES ('f0000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'Standalone', 'published', 'standalonekey0123456789abcd');
INSERT INTO form_submissions (id, tenant_id, form_id, respondent_name, answers, form_snapshot)
VALUES ('c1000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'f0000000-0000-0000-0000-000000000001', 'Standalone person', '{}', '{"form_title":"Standalone","fields":[]}');
