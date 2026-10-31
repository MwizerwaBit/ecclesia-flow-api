INSERT INTO organizations (id, country, currency, display_name, legal_name, slug, timezone, updated_at)
VALUES ('11111111-1111-1111-1111-111111111111', 'US', 'USD', 'Big', 'Big', 'big', 'UTC', now());
INSERT INTO users (id, email, password_hash, first_name, last_name) VALUES ('22222222-2222-2222-2222-222222222222', 'a@example.org', 'x', 'A', 'B');

-- 5,000 events; the first 3,000 have 5 questions each (15,000 questions)
INSERT INTO events (id, tenant_id, title, type, start_date_time, updated_at, created_by_user_id)
SELECT ('e0000000-0000-0000-0000-' || lpad(g::text, 12, '0'))::uuid, '11111111-1111-1111-1111-111111111111', 'Event ' || g, 'event', now(), now(), '22222222-2222-2222-2222-222222222222'
FROM generate_series(1, 5000) g;

INSERT INTO event_registration_fields (tenant_id, event_id, label, field_type, options, required, position)
SELECT '11111111-1111-1111-1111-111111111111', ('e0000000-0000-0000-0000-' || lpad(e::text, 12, '0'))::uuid, 'Question ' || p,
       CASE WHEN p % 3 = 0 THEN 'checkbox' WHEN p % 3 = 1 THEN 'text' ELSE 'single_choice' END,
       CASE WHEN p % 3 = 1 THEN '{}'::text[] ELSE '{A,B,C}'::text[] END, p = 0, p
FROM generate_series(1, 3000) e, generate_series(0, 4) p;

-- 200,000 responses with 6 answers each (one of the labels no longer has a question)
INSERT INTO event_registrations (tenant_id, event_id, respondent_name, respondent_email, answers, created_at)
SELECT '11111111-1111-1111-1111-111111111111', ('e0000000-0000-0000-0000-' || lpad(((g % 5000) + 1)::text, 12, '0'))::uuid,
       'Person ' || g, CASE WHEN g % 4 = 0 THEN NULL ELSE 'p' || g || '@example.com' END,
       jsonb_build_object('Question 0', 'B', 'Question 1', 'text ' || g, 'Question 2', 'A', 'Question 3', jsonb_build_array('A', 'C'),
                          'Question 4', 'text', 'Removed long ago', 'old ' || g),
       now() - (g || ' minutes')::interval
FROM generate_series(1, 200000) g;

ANALYZE event_registration_fields; ANALYZE event_registrations; ANALYZE events;
select (select count(*) from event_registration_fields) fields, (select count(*) from event_registrations) responses, (select count(*) from events) events;
