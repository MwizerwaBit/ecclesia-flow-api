-- Awkward legacy data, to see the copy cope with it.
INSERT INTO organizations (id, country, currency, display_name, legal_name, slug, timezone, updated_at) VALUES
 ('11111111-1111-1111-1111-111111111111', 'US', 'USD', 'Alpha', 'Alpha', 'alpha', 'UTC', now()),
 ('11111111-1111-1111-1111-222222222222', 'US', 'USD', 'Beta', 'Beta', 'beta', 'UTC', now());
INSERT INTO users (id, email, password_hash, first_name, last_name) VALUES ('22222222-2222-2222-2222-222222222222', 'a@example.org', 'x', 'A', 'B');
INSERT INTO hierarchy_units (id, tenant_id, name, type) VALUES ('99999999-9999-9999-9999-999999999999', '11111111-1111-1111-1111-111111111111', 'East', 'Branch');

INSERT INTO events (id, tenant_id, unit_id, title, type, start_date_time, updated_at, created_by_user_id) VALUES
 ('e0000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', NULL, 'Fields and responses', 'event', now(), now(), '22222222-2222-2222-2222-222222222222'),
 ('e0000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', '99999999-9999-9999-9999-999999999999', 'Branch event, fields only', 'event', now(), now(), '22222222-2222-2222-2222-222222222222'),
 ('e0000000-0000-0000-0000-000000000003', '11111111-1111-1111-1111-111111111111', NULL, 'Responses only (questions were later cleared)', 'event', now(), now(), '22222222-2222-2222-2222-222222222222'),
 ('e0000000-0000-0000-0000-000000000004', '11111111-1111-1111-1111-222222222222', NULL, 'Other church', 'event', now(), now(), '22222222-2222-2222-2222-222222222222'),
 ('e0000000-0000-0000-0000-000000000005', '11111111-1111-1111-1111-111111111111', NULL, 'Nothing at all', 'event', now(), now(), '22222222-2222-2222-2222-222222222222'),
 ('e0000000-0000-0000-0000-000000000006', '11111111-1111-1111-1111-111111111111', NULL, 'Duplicate question wording (legacy allowed it)', 'event', now(), now(), '22222222-2222-2222-2222-222222222222');

INSERT INTO event_registration_fields (id, tenant_id, event_id, label, field_type, options, required, position) VALUES
 ('a0000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-000000000001', 'T-shirt', 'single_choice', '{S,M,"L, with comma","XL \"quoted\""}', true, 0),
 ('a0000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-000000000001', 'Allergies', 'checkbox', '{Nuts,Dairy}', false, 1),
 ('a0000000-0000-0000-0000-000000000003', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-000000000001', 'Notes', 'text', '{}', false, 2),
 ('a0000000-0000-0000-0000-000000000004', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-000000000002', 'Only question', 'text', '{}', true, 0),
 ('a0000000-0000-0000-0000-000000000005', '11111111-1111-1111-1111-222222222222', 'e0000000-0000-0000-0000-000000000004', 'Other church question', 'text', '{}', false, 0),
 ('a0000000-0000-0000-0000-000000000006', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-000000000006', 'Same', 'text', '{}', false, 0),
 ('a0000000-0000-0000-0000-000000000007', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-000000000006', 'Same', 'text', '{}', false, 1);

INSERT INTO event_registrations (id, tenant_id, event_id, respondent_name, respondent_email, respondent_phone, answers, created_at) VALUES
 ('b0000000-0000-0000-0000-000000000001', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-000000000001', 'Zoë Álvarez', 'zoe@example.com', '+250 788 123 456', '{"T-shirt": "M", "Allergies": ["Nuts"], "Notes": "line one\nline two \"quoted\" ünïcode ✓"}', now() - interval '3 days'),
 ('b0000000-0000-0000-0000-000000000002', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-000000000001', 'No contact', NULL, NULL, '{}', now() - interval '2 days'),
 ('b0000000-0000-0000-0000-000000000003', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-000000000001', 'Removed questions', NULL, NULL, '{"A question deleted long ago": "x", "first_name": "keys.with.dots and spaces", "Old list": ["a","b"], "Empty list": []}', now() - interval '1 day'),
 ('b0000000-0000-0000-0000-000000000004', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-000000000003', 'Responses only', 'r@example.com', NULL, '{"Gone": "yes"}', now()),
 ('b0000000-0000-0000-0000-000000000005', '11111111-1111-1111-1111-222222222222', 'e0000000-0000-0000-0000-000000000004', 'Other church person', NULL, NULL, '{"Other church question": "hello"}', now()),
 ('b0000000-0000-0000-0000-000000000006', '11111111-1111-1111-1111-111111111111', 'e0000000-0000-0000-0000-000000000006', 'Clashing wording', NULL, NULL, '{"Same": "once"}', now());
