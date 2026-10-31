\pset pager off
\echo === E1. One form for each event with data (expect events 1,2,3,4,6; none for event 5) ===
select right(owner_id::text, 1) as event, tenant_id = '11111111-1111-1111-1111-111111111111' as in_alpha, unit_id is not null as in_branch, status, title from forms order by owner_id;

\echo === E2. Question and response counts per form match the legacy rows (expect fields 3,1,0,1,2 and responses 3,0,1,1,1) ===
select right(f.owner_id::text, 1) as event,
  (select count(*) from form_fields x where x.form_id = f.id) as fields,
  (select count(*) from form_submissions s where s.form_id = f.id) as responses
from forms f order by f.owner_id;

\echo === E3. Options with commas and quotes survive exactly (expect t) ===
select options = array['S','M','L, with comma','XL "quoted"'] as options_identical from form_fields where id = 'a0000000-0000-0000-0000-000000000001';

\echo === E4. Awkward answers survive exactly (expect t t) ===
select answers->>'Notes' = E'line one\nline two "quoted" ünïcode ✓' as multiline_unicode_ok from form_submissions where id = 'b0000000-0000-0000-0000-000000000001';
select answers->>'first_name' = 'keys.with.dots and spaces' as odd_keys_ok from form_submissions where id = 'b0000000-0000-0000-0000-000000000003';

\echo === E5. Snapshot types for labels that no question has any more (expect text/checkbox as the answers shape suggests) ===
select f->>'label' as label, f->>'field_type' as type, (f->>'position')::int as position
from form_submissions s, jsonb_array_elements(s.form_snapshot->'fields') f
where s.id = 'b0000000-0000-0000-0000-000000000003' order by position, label;

\echo === E6. Responses with no questions left get a snapshot built from their answers (expect Gone/text) ===
select f->>'label', f->>'field_type' from form_submissions s, jsonb_array_elements(s.form_snapshot->'fields') f where s.id = 'b0000000-0000-0000-0000-000000000004';

\echo === E7. The legacy duplicate wording was copied (both questions) and nothing failed ===
select count(*) as duplicate_wording_questions from form_fields ff join forms f on f.id = ff.form_id where f.owner_id = 'e0000000-0000-0000-0000-000000000006';

\echo === E8. No form is reachable by another church: tenants on form, fields, responses agree (expect 0 rows) ===
select s.id from form_submissions s join forms f on f.id = s.form_id where f.tenant_id <> s.tenant_id
union all select ff.id from form_fields ff join forms f on f.id = ff.form_id where f.tenant_id <> ff.tenant_id;

\echo === E9. Public keys: unique, 32 characters, hex (expect 5 / 5 / 32 / t) ===
select count(*) as forms, count(distinct public_key) as distinct_keys, min(char_length(public_key)) as min_len, bool_and(public_key ~ '^[0-9a-f]{32}$') as hex from forms;

\echo === E10. The new tables refuse an UPDATE of a response, even from the table owner (expect an error line) ===
update form_submissions set respondent_name = 'changed' where id = 'b0000000-0000-0000-0000-000000000001';
select respondent_name from form_submissions where id = 'b0000000-0000-0000-0000-000000000001';

\echo === E11. A question or response cannot belong to a different church than its form (expect two foreign key errors, then 0) ===
insert into form_fields (tenant_id, form_id, label, field_type, position) select '11111111-1111-1111-1111-222222222222', id, 'Smuggled', 'text', 9 from forms where owner_id = 'e0000000-0000-0000-0000-000000000001';
insert into form_submissions (tenant_id, form_id, respondent_name, form_snapshot) select '11111111-1111-1111-1111-222222222222', id, 'Smuggled', '{}' from forms where owner_id = 'e0000000-0000-0000-0000-000000000001';
select count(*) as smuggled from (select 1 from form_fields where label = 'Smuggled' union all select 1 from form_submissions where respondent_name = 'Smuggled') x;
