-- No schema.prisma changes this time (so, same as migration 0002, Prisma's
-- generated diff was ~230 lines of spurious DROP CONSTRAINT/DROP INDEX for
-- every hand-written FK/index — discarded entirely, see
-- docs/SECURITY_NOTES.md §6). This migration is 100% hand-written.

-- The one public, unauthenticated read in the whole API: the page behind a
-- certificate's printed QR code. RLS has no "allow if you know the secret
-- token" concept, and this request has no tenant context to scope by in the
-- first place — the qr_hash itself is the authorization (same object-
-- capability model as a certificate verification URL on any real platform).
-- SECURITY DEFINER + owned by the migration role (which is exempt from its
-- own RLS as the table owner, same mechanism execute_member_transfer
-- already relies on) is what makes this read possible from the otherwise
-- fully RLS-bound app_tenant role, without granting it bypassrls generally.
create or replace function verify_certificate_by_hash(p_qr_hash text)
returns table (
  id uuid,
  serial_number text,
  issued_at timestamptz,
  is_revoked boolean,
  revoked_at timestamptz,
  template_name text,
  category text,
  member_name text,
  org_name text
)
language sql
security definer
set search_path = public
stable
as $$
  select c.id, c.serial_number, c.issued_at, c.is_revoked, c.revoked_at,
         t.name as template_name, t.category,
         (m.first_name || ' ' || m.last_name) as member_name,
         o.display_name as org_name
  from certificates c
  join certificate_templates t on t.id = c.template_id
  join members m on m.id = c.member_id
  join organizations o on o.id = c.tenant_id
  where c.qr_hash = p_qr_hash
$$;

grant execute on function verify_certificate_by_hash(text) to app_tenant, app_platform;
