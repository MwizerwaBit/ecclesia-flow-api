\set QUIET on
\pset tuples_only on
\pset format unaligned
select (select count(*) from forms) || '/' || (select count(*) from form_fields) || '/' || (select count(*) from form_submissions) || ' forms/fields/submissions; legacy ' || (select count(*) from event_registration_fields) || '/' || (select count(*) from event_registrations);
