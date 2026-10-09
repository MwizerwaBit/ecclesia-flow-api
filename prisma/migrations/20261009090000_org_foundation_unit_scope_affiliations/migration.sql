-- Organisational foundation (docs/ARCHITECTURE.md):
--
--   1. Hierarchy integrity — a unit's parent is in the same organisation,
--      the tree never gets a cycle, and the closure table stays correct when
--      a unit moves (it was only maintained on INSERT, so a moved branch kept
--      its old ancestors — and a scoped admin of the old branch kept access).
--   2. Configurable unit types — each church names its own levels
--      (diocese / parish / outstation, campus / cell, ...) and which level
--      may sit under which.
--   3. Unit-scoped Row-Level Security on people data — the branch boundary
--      the application already enforces, now also enforced by Postgres.
--   4. Unit memberships — a person's home unit and any associate units,
--      with lifecycle and history instead of a single overwritable column.
--   5. Organisation affiliations — an independently registered church joins
--      a parent organisation by mutual consent; the parent gets only what the
--      child granted, never raw access to its records.
--   6. Terminology — the words a church uses for its own structure.
--
-- 100% hand-written, like every migration since 0002 (docs/SECURITY_NOTES.md §6).

-- ═══════════════════════════ 1. Hierarchy integrity ═══════════════════════════

CREATE OR REPLACE FUNCTION guard_hierarchy_unit() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.tenant_id <> OLD.tenant_id THEN
    RAISE EXCEPTION 'A unit cannot move to another organisation' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.parent_id IS NOT NULL THEN
    IF NEW.parent_id = NEW.id THEN
      RAISE EXCEPTION 'A unit cannot be its own parent' USING ERRCODE = 'check_violation';
    END IF;
    -- The FK alone would accept another organisation's unit: FK checks
    -- ignore RLS.
    IF NOT EXISTS (
      SELECT 1 FROM hierarchy_units p WHERE p.id = NEW.parent_id AND p.tenant_id = NEW.tenant_id
    ) THEN
      RAISE EXCEPTION 'The parent unit must belong to the same organisation' USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF TG_OP = 'UPDATE' AND EXISTS (
      SELECT 1 FROM hierarchy_closure WHERE ancestor_id = NEW.id AND descendant_id = NEW.parent_id
    ) THEN
      RAISE EXCEPTION 'A unit cannot be placed under one of its own sub-units' USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER hierarchy_units_guard
BEFORE INSERT OR UPDATE OF parent_id, tenant_id ON "hierarchy_units"
FOR EACH ROW EXECUTE FUNCTION guard_hierarchy_unit();

-- Moving a unit moves its whole subtree: detach the subtree from its old
-- ancestors, then attach it under the new parent's ancestors. Also fires for
-- the FK's ON DELETE SET NULL (a referential action is an UPDATE).
CREATE OR REPLACE FUNCTION move_hierarchy_closure() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.parent_id IS NOT DISTINCT FROM OLD.parent_id THEN
    RETURN NEW;
  END IF;

  DELETE FROM hierarchy_closure c
   WHERE c.descendant_id IN (SELECT descendant_id FROM hierarchy_closure WHERE ancestor_id = NEW.id)
     AND c.ancestor_id NOT IN (SELECT descendant_id FROM hierarchy_closure WHERE ancestor_id = NEW.id);

  IF NEW.parent_id IS NOT NULL THEN
    INSERT INTO hierarchy_closure (ancestor_id, descendant_id, depth)
    SELECT sup.ancestor_id, sub.descendant_id, sup.depth + sub.depth + 1
      FROM hierarchy_closure sup
      JOIN hierarchy_closure sub ON sub.ancestor_id = NEW.id
     WHERE sup.descendant_id = NEW.parent_id;
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER hierarchy_units_closure_move
AFTER UPDATE OF parent_id ON "hierarchy_units"
FOR EACH ROW EXECUTE FUNCTION move_hierarchy_closure();

-- Repair any closure rows already left stale by earlier moves: rebuild the
-- whole table from parent_id (depth-capped, in case a cycle already exists).
DELETE FROM "hierarchy_closure";
INSERT INTO "hierarchy_closure" (ancestor_id, descendant_id, depth)
WITH RECURSIVE tree(ancestor_id, descendant_id, depth) AS (
  SELECT id, id, 0 FROM hierarchy_units
  UNION ALL
  SELECT t.ancestor_id, u.id, t.depth + 1
    FROM tree t
    JOIN hierarchy_units u ON u.parent_id = t.descendant_id AND u.tenant_id = (
      SELECT tenant_id FROM hierarchy_units WHERE id = t.ancestor_id
    )
   WHERE t.depth < 64
)
SELECT DISTINCT ON (ancestor_id, descendant_id) ancestor_id, descendant_id, depth
  FROM tree
 ORDER BY ancestor_id, descendant_id, depth;

-- ═══════════════════════════ 2. Configurable unit types ═══════════════════════════
-- Opt-in: a church with no unit types keeps free-text unit types. Once it
-- defines any, new and edited units must use one, under an allowed parent.
CREATE TABLE "unit_types" (
    "id"                  UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"           UUID NOT NULL,
    "key"                 TEXT NOT NULL,
    "label"               TEXT NOT NULL,
    "plural_label"        TEXT NOT NULL,
    "allowed_parent_keys" TEXT[] NOT NULL DEFAULT '{}',
    "can_be_root"         BOOLEAN NOT NULL DEFAULT false,
    "sort_order"          INTEGER NOT NULL DEFAULT 0,
    "created_at"          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "unit_types_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "unit_types_tenant_key_key" UNIQUE ("tenant_id", "key"),
    CONSTRAINT "unit_types_key_check" CHECK ("key" ~ '^[a-z][a-z0-9_]{0,39}$'),
    CONSTRAINT "unit_types_label_check" CHECK (length("label") BETWEEN 1 AND 60 AND length("plural_label") BETWEEN 1 AND 60)
);
ALTER TABLE "unit_types" ADD CONSTRAINT "unit_types_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;

ALTER TABLE "hierarchy_units" ADD COLUMN "unit_type_id" UUID;
ALTER TABLE "hierarchy_units" ADD CONSTRAINT "hierarchy_units_unit_type_fkey" FOREIGN KEY ("unit_type_id") REFERENCES "unit_types"("id") ON DELETE RESTRICT;
CREATE INDEX "hierarchy_units_unit_type_idx" ON "hierarchy_units" ("unit_type_id");

-- ═══════════════════════════ 3. Unit-scoped session context ═══════════════════════════
-- app.unit_scope is set alongside app.tenant_id for every tenant transaction:
-- 'all' for a whole-church session, or the uuid of the unit the session is
-- scoped to. Unset (or empty) matches nothing — a code path that forgets to
-- set it fails closed, the same way a missing app.tenant_id does.
CREATE OR REPLACE FUNCTION current_unit_scope() RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.unit_scope', true), '')
$$;

-- Runs as the caller, so the closure lookup is itself tenant-isolated.
-- PL/pgSQL rather than SQL on purpose: a SQL function gets inlined and the
-- planner may fold `current_unit_scope()::uuid` early, casting 'all' and
-- failing, even inside a CASE branch that would never run.
CREATE OR REPLACE FUNCTION unit_in_session_scope(p_unit_id uuid) RETURNS boolean
LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_scope text := current_unit_scope();
BEGIN
  IF v_scope IS NULL THEN
    RETURN false;
  ELSIF v_scope = 'all' THEN
    RETURN true;
  ELSIF p_unit_id IS NULL THEN
    RETURN false;
  END IF;
  RETURN EXISTS (
    SELECT 1 FROM hierarchy_closure WHERE ancestor_id = v_scope::uuid AND descendant_id = p_unit_id
  );
END;
$$;

-- ═══════════════════════════ 4. Unit memberships ═══════════════════════════
-- members.unit_id stays as the denormalised "home unit" every module already
-- filters on; this table is the source of history. A trigger keeps the two in
-- step, so every existing write path records history without changes.
CREATE TABLE "unit_memberships" (
    "id"                 UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"          UUID NOT NULL,
    "member_id"          UUID NOT NULL,
    "unit_id"            UUID NOT NULL,
    "kind"               TEXT NOT NULL DEFAULT 'home',
    "status"             TEXT NOT NULL DEFAULT 'active',
    "started_on"         DATE NOT NULL DEFAULT CURRENT_DATE,
    "ended_on"           DATE,
    "end_reason"         TEXT,
    "created_by_user_id" UUID,
    "created_at"         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at"         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "unit_memberships_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "unit_memberships_kind_check" CHECK ("kind" IN ('home', 'associate')),
    CONSTRAINT "unit_memberships_status_check" CHECK ("status" IN ('active', 'suspended', 'transferred', 'ended')),
    CONSTRAINT "unit_memberships_dates_check" CHECK (
      ("status" IN ('active', 'suspended') AND "ended_on" IS NULL)
      OR ("status" IN ('transferred', 'ended') AND "ended_on" IS NOT NULL AND "ended_on" >= "started_on")
    )
);
ALTER TABLE "unit_memberships" ADD CONSTRAINT "unit_memberships_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "unit_memberships" ADD CONSTRAINT "unit_memberships_member_fkey" FOREIGN KEY ("member_id") REFERENCES "members"("id") ON DELETE CASCADE;
-- History outlives nothing silently: a unit with membership history can't be deleted.
ALTER TABLE "unit_memberships" ADD CONSTRAINT "unit_memberships_unit_fkey" FOREIGN KEY ("unit_id") REFERENCES "hierarchy_units"("id") ON DELETE RESTRICT;
ALTER TABLE "unit_memberships" ADD CONSTRAINT "unit_memberships_creator_fkey" FOREIGN KEY ("created_by_user_id") REFERENCES "users"("id") ON DELETE SET NULL;
CREATE UNIQUE INDEX "unit_memberships_one_home_idx" ON "unit_memberships" ("member_id")
  WHERE "kind" = 'home' AND "status" IN ('active', 'suspended');
CREATE UNIQUE INDEX "unit_memberships_one_current_per_unit_idx" ON "unit_memberships" ("member_id", "unit_id")
  WHERE "status" IN ('active', 'suspended');
CREATE INDEX "unit_memberships_unit_idx" ON "unit_memberships" ("tenant_id", "unit_id", "status");
CREATE INDEX "unit_memberships_member_idx" ON "unit_memberships" ("member_id", "started_on" DESC);

CREATE OR REPLACE FUNCTION sync_home_unit_membership() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.unit_id IS NOT DISTINCT FROM OLD.unit_id THEN
    RETURN NEW;
  END IF;
  -- Already recorded (the membership service writes history itself, with a reason).
  IF NEW.unit_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM unit_memberships
     WHERE member_id = NEW.id AND kind = 'home' AND status IN ('active', 'suspended') AND unit_id = NEW.unit_id
  ) THEN
    RETURN NEW;
  END IF;

  UPDATE unit_memberships
     SET status = CASE WHEN NEW.unit_id IS NULL THEN 'ended' ELSE 'transferred' END,
         ended_on = CURRENT_DATE, updated_at = now()
   WHERE member_id = NEW.id AND kind = 'home' AND status IN ('active', 'suspended');

  IF NEW.unit_id IS NOT NULL THEN
    UPDATE unit_memberships
       SET status = 'ended', ended_on = CURRENT_DATE,
           end_reason = coalesce(end_reason, 'Became their home unit'), updated_at = now()
     WHERE member_id = NEW.id AND kind = 'associate' AND unit_id = NEW.unit_id
       AND status IN ('active', 'suspended');
    INSERT INTO unit_memberships (tenant_id, member_id, unit_id, kind, status, started_on, created_by_user_id)
    VALUES (NEW.tenant_id, NEW.id, NEW.unit_id, 'home', 'active', CURRENT_DATE, current_app_user_id());
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER members_sync_home_unit
AFTER INSERT OR UPDATE OF unit_id ON "members"
FOR EACH ROW EXECUTE FUNCTION sync_home_unit_membership();

-- Backfill: everyone currently placed in a unit gets a home membership.
INSERT INTO "unit_memberships" (tenant_id, member_id, unit_id, kind, status, started_on)
SELECT m.tenant_id, m.id, m.unit_id, 'home', 'active', coalesce(m.joined_at, m.created_at::date, CURRENT_DATE)
  FROM members m
 WHERE m.unit_id IS NOT NULL;

-- Cross-church transfers now close the person's unit memberships too.
CREATE OR REPLACE FUNCTION execute_member_transfer(p_transfer_id uuid)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_transfer  member_transfers%ROWTYPE;
  v_new_id    uuid;
BEGIN
  SELECT * INTO v_transfer
  FROM member_transfers
  WHERE id = p_transfer_id AND status = 'approved'
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Transfer % is not in an approved state', p_transfer_id;
  END IF;

  INSERT INTO members (
    id, tenant_id, first_name, last_name, preferred_name, photo_url,
    email, phone, whatsapp, status, date_of_birth, gender,
    marital_status, occupation, transferred_from_member_id, created_at, updated_at
  )
  SELECT
    gen_random_uuid(), v_transfer.to_tenant_id, first_name, last_name, preferred_name, photo_url,
    email, phone, whatsapp, 'active', date_of_birth, gender,
    marital_status, occupation, id, now(), now()
  FROM members
  WHERE id = v_transfer.from_member_id
  RETURNING id INTO v_new_id;

  IF v_transfer.history_scope IN ('include_sacraments', 'full_history') THEN
    UPDATE sacramental_records
       SET tenant_id = v_transfer.to_tenant_id, member_id = v_new_id
     WHERE member_id = v_transfer.from_member_id;
  END IF;

  UPDATE unit_memberships
     SET status = 'transferred', ended_on = CURRENT_DATE,
         end_reason = coalesce(end_reason, 'Transferred to another church'), updated_at = now()
   WHERE member_id = v_transfer.from_member_id AND status IN ('active', 'suspended');

  UPDATE members
     SET status = 'inactive', transferred_to_member_id = v_new_id, updated_at = now()
   WHERE id = v_transfer.from_member_id;

  UPDATE member_transfers
     SET status = 'completed', to_member_id = v_new_id, completed_at = now()
   WHERE id = p_transfer_id;

  RETURN v_new_id;
END;
$$;

-- ═══════════════════════════ 3b. Unit-scoped RLS policies ═══════════════════════════
-- RESTRICTIVE: ANDed with the existing permissive tenant_isolation policy,
-- so these can only ever narrow what a session sees, never widen it.

ALTER TABLE "unit_memberships" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "unit_memberships" USING (tenant_id = current_tenant_id());
CREATE POLICY unit_scope ON "unit_memberships" AS RESTRICTIVE
  USING (unit_in_session_scope(unit_id))
  WITH CHECK (unit_in_session_scope(unit_id));

-- A person is readable from their home unit and from any unit they are an
-- active associate of; only their home unit's staff can change the record.
CREATE POLICY unit_scope_read ON "members" AS RESTRICTIVE FOR SELECT
  USING (
    unit_in_session_scope(unit_id)
    OR EXISTS (
      SELECT 1 FROM unit_memberships um
       WHERE um.member_id = members.id AND um.kind = 'associate' AND um.status = 'active'
         AND unit_in_session_scope(um.unit_id)
    )
  );
CREATE POLICY unit_scope_insert ON "members" AS RESTRICTIVE FOR INSERT
  WITH CHECK (unit_in_session_scope(unit_id));
-- USING checks the row as it is, WITH CHECK the row as it would become: a
-- record can't be moved out of the session's scope.
CREATE POLICY unit_scope_update ON "members" AS RESTRICTIVE FOR UPDATE
  USING (unit_in_session_scope(unit_id))
  WITH CHECK (unit_in_session_scope(unit_id));
CREATE POLICY unit_scope_delete ON "members" AS RESTRICTIVE FOR DELETE
  USING (unit_in_session_scope(unit_id));

-- Personal records hang off a person: visible exactly when the person is
-- (the subquery runs under the members policies above).
CREATE POLICY unit_scope ON "pastoral_notes" AS RESTRICTIVE
  USING (EXISTS (SELECT 1 FROM members m WHERE m.id = pastoral_notes.member_id))
  WITH CHECK (EXISTS (SELECT 1 FROM members m WHERE m.id = pastoral_notes.member_id));
CREATE POLICY unit_scope ON "sacramental_records" AS RESTRICTIVE
  USING (EXISTS (SELECT 1 FROM members m WHERE m.id = sacramental_records.member_id))
  WITH CHECK (EXISTS (SELECT 1 FROM members m WHERE m.id = sacramental_records.member_id));

-- Envelope numbers are unique church-wide, but a scoped session can't see the
-- whole church's people. These answer only the yes/no or next number.
CREATE OR REPLACE FUNCTION tenant_envelope_taken(p_envelope text, p_exclude uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT EXISTS (
    SELECT 1 FROM members
     WHERE tenant_id = current_tenant_id() AND envelope_number = p_envelope
       AND (p_exclude IS NULL OR id <> p_exclude)
  )
$$;

CREATE OR REPLACE FUNCTION tenant_next_envelope_number()
RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT coalesce(max(envelope_number::bigint), 0) + 1
    FROM members
   WHERE tenant_id = current_tenant_id() AND envelope_number ~ '^[0-9]{1,8}$'
$$;

REVOKE ALL ON FUNCTION tenant_envelope_taken(text, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION tenant_next_envelope_number() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION tenant_envelope_taken(text, uuid) TO app_tenant, app_platform;
GRANT EXECUTE ON FUNCTION tenant_next_envelope_number() TO app_tenant, app_platform;

-- ═══════════════════════════ 5. Organisation affiliations ═══════════════════════════
-- Either side may propose; the other side must accept. The child decides what
-- the parent may see (grants), can narrow or widen that at any time, and
-- either side can end the relationship. Rows are never deleted.
CREATE TABLE "organization_affiliations" (
    "id"                   UUID NOT NULL DEFAULT gen_random_uuid(),
    "child_org_id"         UUID NOT NULL,
    "parent_org_id"        UUID NOT NULL,
    "initiated_by_org_id"  UUID NOT NULL,
    "status"               TEXT NOT NULL DEFAULT 'requested',
    "grants"               TEXT[] NOT NULL DEFAULT '{}',
    "message"              TEXT,
    "requested_by_user_id" UUID NOT NULL,
    "requested_at"         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "decided_by_user_id"   UUID,
    "decided_at"           TIMESTAMPTZ(6),
    "decision_note"        TEXT,
    "effective_from"       TIMESTAMPTZ(6),
    "ended_by_user_id"     UUID,
    "ended_at"             TIMESTAMPTZ(6),
    "end_reason"           TEXT,
    CONSTRAINT "organization_affiliations_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "organization_affiliations_distinct_check" CHECK ("child_org_id" <> "parent_org_id"),
    CONSTRAINT "organization_affiliations_initiator_check" CHECK ("initiated_by_org_id" IN ("child_org_id", "parent_org_id")),
    CONSTRAINT "organization_affiliations_status_check" CHECK ("status" IN ('requested', 'active', 'declined', 'withdrawn', 'ended')),
    CONSTRAINT "organization_affiliations_grants_check" CHECK ("grants" <@ ARRAY['aggregate_stats', 'published_events']::text[]),
    CONSTRAINT "organization_affiliations_active_check" CHECK ("status" <> 'active' OR "effective_from" IS NOT NULL)
);
ALTER TABLE "organization_affiliations" ADD CONSTRAINT "organization_affiliations_child_fkey" FOREIGN KEY ("child_org_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "organization_affiliations" ADD CONSTRAINT "organization_affiliations_parent_fkey" FOREIGN KEY ("parent_org_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "organization_affiliations" ADD CONSTRAINT "organization_affiliations_requester_fkey" FOREIGN KEY ("requested_by_user_id") REFERENCES "users"("id");
ALTER TABLE "organization_affiliations" ADD CONSTRAINT "organization_affiliations_decider_fkey" FOREIGN KEY ("decided_by_user_id") REFERENCES "users"("id");
ALTER TABLE "organization_affiliations" ADD CONSTRAINT "organization_affiliations_ender_fkey" FOREIGN KEY ("ended_by_user_id") REFERENCES "users"("id");
-- A church has at most one parent (pending or active) at a time.
CREATE UNIQUE INDEX "organization_affiliations_one_parent_idx" ON "organization_affiliations" ("child_org_id")
  WHERE "status" IN ('requested', 'active');
CREATE INDEX "organization_affiliations_parent_idx" ON "organization_affiliations" ("parent_org_id", "status");

ALTER TABLE "organization_affiliations" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "organization_affiliations"
  USING (current_tenant_id() IN (child_org_id, parent_org_id))
  WITH CHECK (current_tenant_id() IN (child_org_id, parent_org_id));

-- Would making p_parent the parent of p_child close a loop? Walks the active
-- affiliation chain above p_parent, which crosses organisations — hence
-- SECURITY DEFINER; it only ever answers yes/no.
CREATE OR REPLACE FUNCTION affiliation_would_cycle(p_child uuid, p_parent uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  WITH RECURSIVE ancestors(org_id, depth) AS (
    SELECT p_parent, 0
    UNION ALL
    SELECT a.parent_org_id, anc.depth + 1
      FROM organization_affiliations a
      JOIN ancestors anc ON a.child_org_id = anc.org_id
     WHERE a.status = 'active' AND anc.depth < 32
  )
  SELECT EXISTS (SELECT 1 FROM ancestors WHERE org_id = p_child)
$$;

-- What a parent may see of an affiliated church: counts only, and only while
-- the affiliation is active and the child has granted aggregate_stats. The
-- caller's own tenant must be the parent.
CREATE OR REPLACE FUNCTION affiliate_summary(p_affiliation_id uuid)
RETURNS TABLE (
  child_org_id    uuid,
  child_name      text,
  active_members  integer,
  units           integer,
  active_groups   integer,
  upcoming_events integer
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT a.child_org_id,
         o.display_name,
         (SELECT count(*) FROM members m WHERE m.tenant_id = a.child_org_id AND m.status = 'active')::int,
         (SELECT count(*) FROM hierarchy_units hu WHERE hu.tenant_id = a.child_org_id)::int,
         (SELECT count(*) FROM groups g WHERE g.tenant_id = a.child_org_id AND NOT g.is_archived)::int,
         (SELECT count(*) FROM events e WHERE e.tenant_id = a.child_org_id AND e.status = 'published'
             AND e.start_date_time >= now())::int
    FROM organization_affiliations a
    JOIN organizations o ON o.id = a.child_org_id
   WHERE a.id = p_affiliation_id
     AND a.parent_org_id = current_tenant_id()
     AND a.status = 'active'
     AND 'aggregate_stats' = ANY (a.grants)
$$;

-- Published gatherings the child chose to share: public or members-only,
-- never private group events, never drafts.
CREATE OR REPLACE FUNCTION affiliate_published_events(p_affiliation_id uuid)
RETURNS TABLE (
  id              uuid,
  title           text,
  type            text,
  location        text,
  start_date_time timestamptz,
  end_date_time   timestamptz
)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT e.id, e.title, e.type, e.location, e.start_date_time, e.end_date_time
    FROM organization_affiliations a
    JOIN events e ON e.tenant_id = a.child_org_id
   WHERE a.id = p_affiliation_id
     AND a.parent_org_id = current_tenant_id()
     AND a.status = 'active'
     AND 'published_events' = ANY (a.grants)
     AND e.status = 'published'
     AND e.visibility IN ('public', 'members')
     AND e.start_date_time >= now() - interval '1 day'
   ORDER BY e.start_date_time
   LIMIT 200
$$;

REVOKE ALL ON FUNCTION affiliation_would_cycle(uuid, uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION affiliate_summary(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION affiliate_published_events(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION affiliation_would_cycle(uuid, uuid) TO app_tenant, app_platform;
GRANT EXECUTE ON FUNCTION affiliate_summary(uuid) TO app_tenant, app_platform;
GRANT EXECUTE ON FUNCTION affiliate_published_events(uuid) TO app_tenant, app_platform;

-- ═══════════════════════════ 6. Terminology ═══════════════════════════
CREATE TABLE "organization_configurations" (
    "tenant_id"          UUID NOT NULL,
    "terminology"        JSONB NOT NULL DEFAULT '{}',
    "updated_by_user_id" UUID,
    "updated_at"         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "organization_configurations_pkey" PRIMARY KEY ("tenant_id"),
    CONSTRAINT "organization_configurations_terminology_check" CHECK (jsonb_typeof("terminology") = 'object')
);
ALTER TABLE "organization_configurations" ADD CONSTRAINT "organization_configurations_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "organization_configurations" ADD CONSTRAINT "organization_configurations_updater_fkey" FOREIGN KEY ("updated_by_user_id") REFERENCES "users"("id") ON DELETE SET NULL;

ALTER TABLE "unit_types" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "unit_types" USING (tenant_id = current_tenant_id());
ALTER TABLE "organization_configurations" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "organization_configurations" USING (tenant_id = current_tenant_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON
  "unit_types", "unit_memberships", "organization_affiliations", "organization_configurations"
TO app_tenant, app_platform;

-- ═══════════════════════════ Permissions ═══════════════════════════
-- Mirrors app/modules/rbac/models.py SYSTEM_ROLE_PERMISSIONS.
INSERT INTO "role_permissions" ("role_id", "permission") VALUES
  ('00000000-0000-0000-0000-000000000003', 'affiliations:read'),
  ('00000000-0000-0000-0000-000000000004', 'affiliations:read'),
  ('00000000-0000-0000-0000-000000000004', 'affiliations:manage')
ON CONFLICT DO NOTHING;
