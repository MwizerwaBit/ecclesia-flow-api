-- Forms module (TODO.md, Forms & Registrations F1).
--
-- One place for questions and the answers to them, used two ways:
--   - standalone forms a church builds and shares (a visitor card, a survey);
--   - event-linked forms, which is how an event's registration questions are
--     stored from now on (owner_kind = 'event', owner_id = the event's id).
--
-- The forms tables know nothing about events: owner_kind/owner_id are an opaque
-- pointer the owning module supplies, with no foreign key. Events decides who may
-- change or read an event's form and whether the event is open for sign-ups; Forms
-- decides whether the form takes answers and what they must look like.
--
-- A submission carries its own copy of the questions it answered (form_snapshot),
-- and the database refuses any UPDATE of a submission, so editing or deleting a
-- question later can never change what someone was asked or what they said.
--
-- Expand/contract: event_registration_fields and event_registrations are copied into
-- the new tables by the NEXT migration (20261031090100_forms_backfill) and left in
-- place, unused, so the deploy can be rolled back. A later migration drops them once
-- this has been live for a release.
--
-- This one is structure only and takes a moment. It is kept apart from the copy on
-- purpose: adding the foreign keys locks organizations and hierarchy_units against
-- writes until the transaction ends, and the copy can take a minute on a big install.

CREATE TABLE "forms" (
    "id"                 UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"          UUID NOT NULL,
    -- NULL = church-wide, like events and groups.
    "unit_id"            UUID,
    "owner_kind"         TEXT,
    "owner_id"           UUID,
    "title"              TEXT NOT NULL,
    "description"        TEXT,
    "status"             TEXT NOT NULL DEFAULT 'draft',
    -- The unguessable part of a standalone form's public link.
    "public_key"         TEXT NOT NULL,
    "created_by_user_id" UUID,
    "created_at"         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at"         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "published_at"       TIMESTAMPTZ(6),
    "closed_at"          TIMESTAMPTZ(6),
    CONSTRAINT "forms_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "forms_status_check" CHECK ("status" IN ('draft', 'published', 'closed')),
    CONSTRAINT "forms_owner_kind_check" CHECK ("owner_kind" IS NULL OR "owner_kind" IN ('event')),
    CONSTRAINT "forms_owner_pair_check" CHECK (("owner_kind" IS NULL) = ("owner_id" IS NULL)),
    CONSTRAINT "forms_public_key_check" CHECK (char_length("public_key") BETWEEN 16 AND 64)
);
ALTER TABLE "forms" ADD CONSTRAINT "forms_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "forms" ADD CONSTRAINT "forms_unit_fkey" FOREIGN KEY ("unit_id") REFERENCES "hierarchy_units"("id") ON DELETE SET NULL;
CREATE UNIQUE INDEX "forms_public_key_key" ON "forms" ("public_key");
-- Lets form_fields and form_submissions point at (id, tenant_id), so a row can never belong to a different
-- church than its form, whatever the application does.
CREATE UNIQUE INDEX "forms_id_tenant_key" ON "forms" ("id", "tenant_id");
-- At most one form per owner (one registration form per event).
CREATE UNIQUE INDEX "forms_owner_key" ON "forms" ("tenant_id", "owner_kind", "owner_id") WHERE "owner_kind" IS NOT NULL;
CREATE INDEX "forms_tenant_idx" ON "forms" ("tenant_id", "created_at" DESC) WHERE "owner_kind" IS NULL;

CREATE TABLE "form_fields" (
    "id"         UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"  UUID NOT NULL,
    "form_id"    UUID NOT NULL,
    "label"      TEXT NOT NULL,
    "field_type" TEXT NOT NULL,
    "options"    TEXT[] NOT NULL DEFAULT '{}',
    "required"   BOOLEAN NOT NULL DEFAULT false,
    "position"   INTEGER NOT NULL,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "form_fields_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "form_fields_type_check" CHECK ("field_type" IN ('text', 'single_choice', 'checkbox'))
);
ALTER TABLE "form_fields" ADD CONSTRAINT "form_fields_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "form_fields" ADD CONSTRAINT "form_fields_form_fkey" FOREIGN KEY ("form_id", "tenant_id") REFERENCES "forms"("id", "tenant_id") ON DELETE CASCADE;
CREATE INDEX "form_fields_form_idx" ON "form_fields" ("form_id", "position");

CREATE TABLE "form_submissions" (
    "id"               UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"        UUID NOT NULL,
    "form_id"          UUID NOT NULL,
    "respondent_name"  TEXT NOT NULL,
    "respondent_email" TEXT,
    "respondent_phone" TEXT,
    -- Keyed by the question's label as it was when the answer was given.
    "answers"          JSONB NOT NULL DEFAULT '{}',
    -- {"form_title": ..., "fields": [{label, field_type, options, required, position}]}
    -- as they were at that moment. "reconstructed": true marks a submission
    -- copied from the old registration tables, where the questions as they were
    -- were never kept: it holds the questions at the time of the copy plus any
    -- label found only in the answers.
    "form_snapshot"    JSONB NOT NULL,
    "created_at"       TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "form_submissions_pkey" PRIMARY KEY ("id")
);
ALTER TABLE "form_submissions" ADD CONSTRAINT "form_submissions_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "form_submissions" ADD CONSTRAINT "form_submissions_form_fkey" FOREIGN KEY ("form_id", "tenant_id") REFERENCES "forms"("id", "tenant_id") ON DELETE CASCADE;
CREATE INDEX "form_submissions_form_idx" ON "form_submissions" ("form_id", "created_at" DESC, "id");

-- ═══════════════════════════ Row-level security ═══════════════════════════
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['forms', 'form_fields', 'form_submissions'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_tenant_id())', t);
  END LOOP;
END $$;

-- Branch scope, the same rules as events and groups: church-wide forms (no unit)
-- are readable by everyone with access, changed only by whole-church sessions.
CREATE POLICY unit_scope_read ON "forms" AS RESTRICTIVE FOR SELECT
  USING (unit_id IS NULL OR unit_in_session_scope(unit_id));
CREATE POLICY unit_scope_insert ON "forms" AS RESTRICTIVE FOR INSERT
  WITH CHECK (unit_in_session_scope(unit_id));
CREATE POLICY unit_scope_update ON "forms" AS RESTRICTIVE FOR UPDATE
  USING (unit_in_session_scope(unit_id))
  WITH CHECK (unit_in_session_scope(unit_id));
CREATE POLICY unit_scope_delete ON "forms" AS RESTRICTIVE FOR DELETE
  USING (unit_in_session_scope(unit_id));

-- Questions and answers follow the form: what you can't see the form for, you can't see these for.
CREATE POLICY unit_scope ON "form_fields" AS RESTRICTIVE
  USING (EXISTS (SELECT 1 FROM forms f WHERE f.id = form_fields.form_id))
  WITH CHECK (EXISTS (SELECT 1 FROM forms f WHERE f.id = form_fields.form_id));
CREATE POLICY unit_scope ON "form_submissions" AS RESTRICTIVE
  USING (EXISTS (SELECT 1 FROM forms f WHERE f.id = form_submissions.form_id))
  WITH CHECK (EXISTS (SELECT 1 FROM forms f WHERE f.id = form_submissions.form_id));

GRANT SELECT, INSERT, UPDATE, DELETE ON "forms", "form_fields" TO app_tenant, app_platform;
GRANT SELECT, INSERT ON "form_submissions" TO app_tenant, app_platform;
-- A submission is a record of what someone said: add-only. (Table defaults may
-- have granted more; take it back, as for notification_deliveries.)
REVOKE UPDATE, DELETE ON "form_submissions" FROM app_tenant, app_platform;

CREATE FUNCTION form_submissions_refuse_update() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'form submissions cannot be changed' USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER form_submissions_immutable BEFORE UPDATE ON "form_submissions"
  FOR EACH ROW EXECUTE FUNCTION form_submissions_refuse_update();

-- ═══════════════════════════ Permissions ═══════════════════════════
-- forms:read    see forms and their questions
-- forms:manage  create, edit, publish, close and delete standalone forms
-- forms:responses  read and export what people submitted (personal details)
-- Event forms follow the event: events:update (and the event's branch).
INSERT INTO "role_permissions" ("role_id", "permission")
SELECT r.role_id, p.permission
FROM (VALUES
  ('00000000-0000-0000-0000-000000000002'::uuid),
  ('00000000-0000-0000-0000-000000000003'::uuid),
  ('00000000-0000-0000-0000-000000000004'::uuid)
) AS r(role_id)
CROSS JOIN (VALUES ('forms:read'), ('forms:manage'), ('forms:responses')) AS p(permission)
ON CONFLICT DO NOTHING;

COMMENT ON TABLE "event_registration_fields" IS 'Superseded by form_fields (20261031090000). Unused; to be dropped.';
COMMENT ON TABLE "event_registrations" IS 'Superseded by form_submissions (20261031090000). Unused; to be dropped.';
