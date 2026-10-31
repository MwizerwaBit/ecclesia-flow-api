\pset pager off
\echo === C1. The late response for an event that had a form was copied, marked reconstructed (expect t t) ===
select count(*) = 1 as copied, bool_and(form_snapshot->>'reconstructed' = 'true') as reconstructed from form_submissions where id = 'b1000000-0000-0000-0000-000000000001';
\echo === C2. An event that only got data in the window now has a form, its question and its response (expect 1 / Brand new question / 1) ===
select (select count(*) from forms where owner_id = 'e0000000-0000-0000-0000-000000000005') as forms,
       (select string_agg(ff.label, ',') from form_fields ff join forms f on f.id = ff.form_id where f.owner_id = 'e0000000-0000-0000-0000-000000000005') as questions,
       (select count(*) from form_submissions s join forms f on f.id = s.form_id where f.owner_id = 'e0000000-0000-0000-0000-000000000005') as responses;
\echo === C3. Questions the old release replaced in an untouched form were taken over (expect Replaced question) ===
select string_agg(ff.label, ',') from form_fields ff join forms f on f.id = ff.form_id where f.owner_id = 'e0000000-0000-0000-0000-000000000002';
\echo === C4. A form the new release had already edited keeps ITS questions (expect Edited by new release) ===
select string_agg(ff.label, ',') from form_fields ff join forms f on f.id = ff.form_id where f.owner_id = 'e0000000-0000-0000-0000-000000000006';
\echo === C5. Every legacy response is now present with identical content (expect 0 rows) ===
select r.id from event_registrations r left join form_submissions s on s.id = r.id
where s.id is null or s.answers <> r.answers or s.respondent_name <> r.respondent_name or s.respondent_email is distinct from r.respondent_email;
