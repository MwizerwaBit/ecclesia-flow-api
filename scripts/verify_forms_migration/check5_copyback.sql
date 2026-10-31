\pset pager off
\echo === B1. Event 1s legacy questions now equal the forms, including the new one (expect T-shirt,Allergies,Notes,New world question) ===
select string_agg(label, ',' order by position) from event_registration_fields where event_id = 'e0000000-0000-0000-0000-000000000001';
\echo === B2. The new-release response is in the legacy table, with its content (expect 1 / New world person) ===
select count(*), min(respondent_name) from event_registrations where id = 'c1000000-0000-0000-0000-000000000001';
\echo === B3. A standalone forms response is NOT copied (the old release has nowhere to put it) (expect 0) ===
select count(*) from event_registrations where id = 'c1000000-0000-0000-0000-000000000002';
\echo === B4. Event 6 legacy questions follow the form (expect Edited by new release) ===
select string_agg(label, ',') from event_registration_fields where event_id = 'e0000000-0000-0000-0000-000000000006';
\echo === B5. Responses that were already in the legacy table are byte-identical to before (expect t) ===
select md5(string_agg(r::text, '|' order by id)) = :'before' as unchanged from event_registrations r where id::text like 'b0%';
\echo === B6. The forms tables are all still there (nothing dropped) (expect 3) ===
select count(*) from pg_class where relname in ('forms','form_fields','form_submissions') and relkind = 'r';
