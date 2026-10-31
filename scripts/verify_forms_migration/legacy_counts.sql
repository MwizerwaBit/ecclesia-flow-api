\set QUIET on
\pset tuples_only on
\pset format unaligned
select (select count(*) from event_registration_fields) || '/' || (select count(*) from event_registrations) || ' fields/responses';
