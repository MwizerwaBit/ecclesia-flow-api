\pset pager off
\echo === S1. Objects exist (expect 3 tables, 1 function, 1 trigger) ===
select count(*) filter (where relname in ('forms','form_fields','form_submissions')) as tables from pg_class where relkind='r' and relnamespace='public'::regnamespace;
select count(*) as trigger_fn from pg_proc where proname='form_submissions_refuse_update';
select tgname, tgenabled from pg_trigger where tgrelid='form_submissions'::regclass and not tgisinternal;

\echo === S2. Row level security enabled on all three; policies per table ===
select c.relname, c.relrowsecurity as rls_on,
       (select string_agg(p.polname || case when p.polpermissive then '' else '(restrictive)' end, ', ' order by p.polname) from pg_policy p where p.polrelid=c.oid) as policies
from pg_class c where c.relname in ('forms','form_fields','form_submissions') order by 1;

\echo === S3. Privileges: submissions are add-only for both app roles (expect SELECT,INSERT true; UPDATE,DELETE false) ===
select r.rolname,
  has_table_privilege(r.rolname,'form_submissions','SELECT') as sel,
  has_table_privilege(r.rolname,'form_submissions','INSERT') as ins,
  has_table_privilege(r.rolname,'form_submissions','UPDATE') as upd,
  has_table_privilege(r.rolname,'form_submissions','DELETE') as del
from pg_roles r where r.rolname in ('app_tenant','app_platform') order by 1;
select r.rolname, 'forms' as t,
  has_table_privilege(r.rolname,'forms','SELECT') as sel, has_table_privilege(r.rolname,'forms','INSERT') as ins,
  has_table_privilege(r.rolname,'forms','UPDATE') as upd, has_table_privilege(r.rolname,'forms','DELETE') as del
from pg_roles r where r.rolname in ('app_tenant','app_platform') order by 1;

\echo === S4. Constraints and indexes ===
select conrelid::regclass as tbl, conname, contype from pg_constraint where conrelid in ('forms'::regclass,'form_fields'::regclass,'form_submissions'::regclass) order by 1,2;
select indexname from pg_indexes where tablename in ('forms','form_fields','form_submissions') order by 1;

\echo === S5. Permissions seeded for staff, board, leader (expect 9 rows) ===
select role_id, permission from role_permissions where permission like 'forms:%' order by 1,2;

\echo === S6. Legacy tables are marked superseded and still present ===
select c.relname, obj_description(c.oid) from pg_class c where c.relname in ('event_registration_fields','event_registrations') order by 1;

\echo === S7. Migration recorded as finished ===
select migration_name, finished_at is not null as finished, rolled_back_at is null as not_rolled_back from _prisma_migrations where migration_name='20261031090000_forms_module';

\echo === S8. Child rows are tied to their forms church by a composite key ===
select conname, pg_get_constraintdef(oid) from pg_constraint where conname in ('form_fields_form_fkey','form_submissions_form_fkey') order by 1;
