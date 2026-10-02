"""security and seed

Row-Level Security for every tenant table, the closure-table maintenance
trigger, the cross-tenant member-transfer function, system role/permission
seed data, and the two request-serving database roles
(docs/database-design.md §8, plan.md Phase 0/1/3).

Revision ID: c7dc285932d3
Revises: 55085e71a204
Create Date: 2026-10-01 23:18:02.071122

"""
from collections.abc import Sequence

from sqlalchemy import text

from alembic import op
from app.core.config import get_settings
from app.modules.rbac.models import SYSTEM_ROLE_NAMES, SYSTEM_ROLE_PERMISSIONS

revision: str = "c7dc285932d3"
down_revision: str | None = "55085e71a204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables that get their own hand-written policy below instead of the generic
# "tenant_id = current_setting(...)" loop — either because they're legitimately
# cross-tenant (member_transfers), need a self-visibility carve-out
# (tenant_memberships), or have no tenant_id column at all (hierarchy_closure,
# media_attachments) despite being fully tenant-scoped through a parent row.
HAND_WRITTEN_RLS_TABLES = ("member_transfers", "tenant_memberships")


def upgrade() -> None:
    # ── 0. Two tiny stable helper functions every policy below calls instead
    #    of inlining `current_setting(...)::uuid` directly. Why this exists:
    #    `set_config(name, value, true)` ("SET LOCAL" semantics) does NOT
    #    revert an unset custom GUC back to NULL after commit — the first time
    #    a custom GUC is touched on a session, its post-transaction baseline
    #    becomes an empty string, not NULL. On a pooled connection reused
    #    across unrelated requests, that means a later transaction which never
    #    sets app.tenant_id at all can still see a literal '' left over from
    #    an earlier one, and bare `''::uuid` is a hard cast error, not a
    #    harmless non-match. `nullif(..., '')` closes that gap once, here,
    #    rather than at every one of the ~20 call sites below. ───────────────
    op.execute(
        """
        create or replace function current_tenant_id() returns uuid
        language sql stable as $$
          select nullif(current_setting('app.tenant_id', true), '')::uuid
        $$;
        """
    )
    op.execute(
        """
        create or replace function current_app_user_id() returns uuid
        language sql stable as $$
          select nullif(current_setting('app.user_id', true), '')::uuid
        $$;
        """
    )

    # ── 1. Generic RLS pass — every table with a NOT NULL tenant_id column,
    #    except the ones handled explicitly below. ──────────────────────────
    op.execute(
        f"""
        do $$
        declare
          r record;
        begin
          for r in
            select distinct c.relname as table_name
            from pg_attribute a
            join pg_class c on c.oid = a.attrelid
            join pg_namespace n on n.oid = c.relnamespace
            where a.attname = 'tenant_id'
              and a.attnotnull
              and c.relkind = 'r'
              and n.nspname = 'public'
              and c.relname not in {HAND_WRITTEN_RLS_TABLES!r}
          loop
            execute format('alter table %I enable row level security', r.table_name);
            execute format(
              'create policy tenant_isolation on %I using (tenant_id = current_tenant_id())',
              r.table_name
            );
          end loop;
        end $$;
        """
    )

    # ── 2. tenant_memberships — tenant-scoped PLUS self-visible, so a user
    #    can list every church they belong to (login, the tenant switcher)
    #    without that becoming a general cross-tenant read. ─────────────────
    op.execute("alter table tenant_memberships enable row level security")
    op.execute(
        """
        create policy tenant_isolation on tenant_memberships
        using (tenant_id = current_tenant_id() or user_id = current_app_user_id())
        with check (tenant_id = current_tenant_id())
        """
    )

    # ── 3. member_transfers — visible if the session's tenant is either side. ─
    op.execute("alter table member_transfers enable row level security")
    op.execute(
        """
        create policy tenant_isolation on member_transfers
        using (current_tenant_id() in (from_tenant_id, to_tenant_id))
        """
    )

    # ── 4. roles — nullable tenant_id (system roles). The database-design.md
    #    generic loop's `attnotnull` condition silently skips this table, which
    #    would leave every tenant's custom roles readable by every other
    #    tenant's connection. Closed here explicitly rather than left as the
    #    gap the generic loop produces. ──────────────────────────────────────
    op.execute("alter table roles enable row level security")
    op.execute(
        """
        create policy tenant_isolation on roles
        using (tenant_id is null or tenant_id = current_tenant_id())
        with check (tenant_id is null or tenant_id = current_tenant_id())
        """
    )

    # ── 5. role_permissions — no tenant_id column at all; scoped through its
    #    parent role. ────────────────────────────────────────────────────────
    op.execute("alter table role_permissions enable row level security")
    op.execute(
        """
        create policy tenant_isolation on role_permissions
        using (
          exists (
            select 1 from roles r
            where r.id = role_permissions.role_id
              and (r.tenant_id is null or r.tenant_id = current_tenant_id())
          )
        )
        """
    )

    # ── 6. audit_logs — nullable tenant_id (platform-level rows). A null
    #    row is simply invisible to an app_tenant connection (tenant_id = null
    #    never matches), which is the desired behavior: tenants don't see
    #    platform-level audit entries. app_platform bypasses RLS entirely. ───
    op.execute("alter table audit_logs enable row level security")
    op.execute(
        """
        create policy tenant_isolation on audit_logs
        using (tenant_id = current_tenant_id())
        """
    )

    # ── 7. hierarchy_closure / media_attachments — no tenant_id column;
    #    scoped through the parent table that does carry one. ────────────────
    op.execute("alter table hierarchy_closure enable row level security")
    op.execute(
        """
        create policy tenant_isolation on hierarchy_closure
        using (
          exists (
            select 1 from hierarchy_units hu
            where hu.id = hierarchy_closure.ancestor_id
              and hu.tenant_id = current_tenant_id()
          )
        )
        """
    )
    op.execute("alter table media_attachments enable row level security")
    op.execute(
        """
        create policy tenant_isolation on media_attachments
        using (
          exists (
            select 1 from media_assets ma
            where ma.id = media_attachments.media_id
              and ma.tenant_id = current_tenant_id()
          )
        )
        """
    )

    # ── 8. Closure-table maintenance trigger — every hierarchy_units insert
    #    gets a depth-0 self-row plus a depth+1 row joining it to every
    #    ancestor of its parent, so "all descendants of X" is ever only a
    #    single indexed lookup against hierarchy_closure (plan.md Phase 2). ──
    op.execute(
        """
        create or replace function maintain_hierarchy_closure() returns trigger
        language plpgsql as $$
        begin
          insert into hierarchy_closure (ancestor_id, descendant_id, depth)
          values (new.id, new.id, 0);

          if new.parent_id is not null then
            insert into hierarchy_closure (ancestor_id, descendant_id, depth)
            select ancestor_id, new.id, depth + 1
            from hierarchy_closure
            where descendant_id = new.parent_id;
          end if;

          return new;
        end;
        $$;
        """
    )
    op.execute(
        """
        create trigger hierarchy_units_closure_insert
        after insert on hierarchy_units
        for each row execute function maintain_hierarchy_closure();
        """
    )

    # ── 9. The one cross-tenant write path — member transfer execution.
    #    SECURITY DEFINER so this function (and only this function) may write
    #    across the tenant boundary; every other code path stays single-tenant
    #    (docs/database-design.md §6). ────────────────────────────────────────
    op.execute(
        """
        create or replace function execute_member_transfer(p_transfer_id uuid)
        returns uuid
        language plpgsql
        security definer
        set search_path = public
        as $$
        declare
          v_transfer  member_transfers%rowtype;
          v_new_id    uuid;
        begin
          select * into v_transfer
          from member_transfers
          where id = p_transfer_id and status = 'approved'
          for update;

          if not found then
            raise exception 'Transfer % is not in an approved state', p_transfer_id;
          end if;

          insert into members (
            id, tenant_id, first_name, last_name, preferred_name, photo_url,
            email, phone, whatsapp, status, date_of_birth, gender,
            marital_status, occupation, transferred_from_member_id, created_at, updated_at
          )
          select
            gen_random_uuid(), v_transfer.to_tenant_id, first_name, last_name, preferred_name, photo_url,
            email, phone, whatsapp, 'active', date_of_birth, gender,
            marital_status, occupation, id, now(), now()
          from members
          where id = v_transfer.from_member_id
          returning id into v_new_id;

          if v_transfer.history_scope in ('include_sacraments', 'full_history') then
            update sacramental_records
               set tenant_id = v_transfer.to_tenant_id, member_id = v_new_id
             where member_id = v_transfer.from_member_id;
          end if;

          update members
             set status = 'inactive', transferred_to_member_id = v_new_id, updated_at = now()
           where id = v_transfer.from_member_id;

          update member_transfers
             set status = 'completed', to_member_id = v_new_id, completed_at = now()
           where id = p_transfer_id;

          return v_new_id;
        end;
        $$;
        """
    )

    # ── 10. Seed the three system roles + their permissions — copied straight
    #     from src/hooks/useRole.ts's ROLE_PERMISSIONS via
    #     app.modules.rbac.models.SYSTEM_ROLE_PERMISSIONS, so this migration
    #     can never silently drift from what that Python constant says. ──────
    conn = op.get_bind()
    for role_id, name in SYSTEM_ROLE_NAMES.items():
        conn.execute(
            text(
                "insert into roles (id, tenant_id, name, is_system, created_at) "
                "values (:id, null, :name, true, now())"
            ),
            {"id": str(role_id), "name": name},
        )
        for permission in SYSTEM_ROLE_PERMISSIONS[role_id]:
            conn.execute(
                text("insert into role_permissions (role_id, permission) values (:role_id, :permission)"),
                {"role_id": str(role_id), "permission": permission},
            )

    # ── 11. Two database roles matching the two frontend access paths.
    #     Passwords come from Settings. asyncpg's protocol can't bind
    #     parameters into a DO block's function body, so they're escaped
    #     (doubling any single quote, the standard Postgres string-literal
    #     escape) and inlined directly rather than passed as query params. ────
    settings = get_settings()

    def _pg_escape(value: str) -> str:
        return value.replace("'", "''")

    tenant_pw = _pg_escape(settings.app_tenant_db_password)
    platform_pw = _pg_escape(settings.app_platform_db_password)
    op.execute(
        f"""
        do $$
        begin
          if not exists (select from pg_roles where rolname = 'app_tenant') then
            create role app_tenant login password '{tenant_pw}';
          end if;
          if not exists (select from pg_roles where rolname = 'app_platform') then
            create role app_platform login password '{platform_pw}' bypassrls;
          end if;
        end $$;
        """
    )
    op.execute("grant usage on schema public to app_tenant, app_platform")
    op.execute("grant select, insert, update, delete on all tables in schema public to app_tenant, app_platform")
    op.execute("grant execute on function execute_member_transfer(uuid) to app_tenant, app_platform")
    op.execute(
        "alter default privileges in schema public grant select, insert, update, delete on tables "
        "to app_tenant, app_platform"
    )


def downgrade() -> None:
    op.execute(
        """
        do $$
        declare
          r record;
        begin
          for r in select tablename from pg_policies where policyname = 'tenant_isolation'
          loop
            execute format('drop policy tenant_isolation on %I', r.tablename);
            execute format('alter table %I disable row level security', r.tablename);
          end loop;
        end $$;
        """
    )
    op.execute("delete from role_permissions")
    op.execute("delete from roles where is_system = true")
    op.execute("revoke execute on function execute_member_transfer(uuid) from app_tenant, app_platform")
    op.execute("drop function if exists execute_member_transfer(uuid)")
    op.execute("drop trigger if exists hierarchy_units_closure_insert on hierarchy_units")
    op.execute("drop function if exists maintain_hierarchy_closure()")
    op.execute("drop owned by app_tenant")
    op.execute("drop owned by app_platform")
    op.execute("drop role if exists app_tenant")
    op.execute("drop role if exists app_platform")
    op.execute("drop function if exists current_tenant_id()")
    op.execute("drop function if exists current_app_user_id()")