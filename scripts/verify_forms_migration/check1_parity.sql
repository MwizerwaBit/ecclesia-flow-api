\pset pager off
\echo === 1. Volumes (dev database) ===
select 'legacy fields' as what, count(*) from event_registration_fields
union all select 'legacy registrations', count(*) from event_registrations
union all select 'legacy events with data', count(*) from events e where exists (select 1 from event_registration_fields f where f.event_id=e.id) or exists (select 1 from event_registrations r where r.event_id=e.id)
union all select 'event forms', count(*) from forms where owner_kind='event'
union all select 'form_fields (event forms)', count(*) from form_fields ff join forms f on f.id=ff.form_id where f.owner_kind='event'
union all select 'submissions marked reconstructed', count(*) from form_submissions where form_snapshot->>'reconstructed' = 'true'
union all select 'submissions not reconstructed (made after the migration)', count(*) from form_submissions where coalesce(form_snapshot->>'reconstructed','false') <> 'true';

\echo === 2. Every legacy event with data has exactly one event form, same tenant and branch (expect 0 rows) ===
select e.id as event_id from events e
where (exists (select 1 from event_registration_fields f where f.event_id=e.id) or exists (select 1 from event_registrations r where r.event_id=e.id))
  and (select count(*) from forms fm where fm.owner_kind='event' and fm.owner_id=e.id and fm.tenant_id=e.tenant_id and fm.unit_id is not distinct from e.unit_id) <> 1;

\echo === 3. Every legacy question exists in form_fields with the same id and content (expect 0 rows) ===
select f.id from event_registration_fields f
left join form_fields ff on ff.id=f.id
left join forms fm on fm.id=ff.form_id
where ff.id is null or fm.owner_id <> f.event_id or fm.tenant_id <> f.tenant_id
   or ff.label <> f.label or ff.field_type <> f.field_type or ff.options <> f.options
   or ff.required <> f.required or ff.position <> f.position or ff.tenant_id <> f.tenant_id;

\echo === 4. Every legacy response exists in form_submissions with the same id and identical content (expect 0 rows) ===
select r.id from event_registrations r
left join form_submissions s on s.id=r.id
left join forms fm on fm.id=s.form_id
where s.id is null or fm.owner_id <> r.event_id or s.tenant_id <> r.tenant_id
   or s.respondent_name <> r.respondent_name
   or s.respondent_email is distinct from r.respondent_email
   or s.respondent_phone is distinct from r.respondent_phone
   or s.answers <> r.answers
   or s.created_at <> r.created_at;

\echo === 5. Copied submissions: snapshot is well formed, lists every answered label, and carries every legacy question (expect 0 rows) ===
select s.id from form_submissions s
where s.form_snapshot->>'reconstructed' = 'true'
  and ( jsonb_typeof(s.form_snapshot->'fields') <> 'array'
     or coalesce(s.form_snapshot->>'form_title','') = ''
     or exists (select 1 from jsonb_object_keys(s.answers) k
                 where not exists (select 1 from jsonb_array_elements(s.form_snapshot->'fields') f where f->>'label' = k)) );

\echo === 6. Answer-type sanity across the copied data (counts) ===
select jsonb_typeof(v) as answer_json_type, count(*) from event_registrations r, jsonb_each(r.answers) a(k, v) group by 1;

\echo === 7. Labels that clash inside one event (legacy data the new unique-wording rule would not allow to be re-saved) ===
select event_id, lower(label) as label, count(*) from event_registration_fields group by 1,2 having count(*) > 1;

\echo === 8. Legacy tables untouched by anything since the migration (nothing should write them): newest legacy row vs migration time ===
select max(created_at) as newest_legacy_response from event_registrations;
select started_at as forms_migration_started from _prisma_migrations where migration_name = '20261031090000_forms_module';
