-- Member registration fields + Groups.
--
-- Hand-written (same conventions as the init migration's hand-written half):
-- every new tenant table gets RLS keyed on current_tenant_id(), and the
-- system roles get the new groups permissions so the frontend's useRole map
-- and the API's enforcement stay identical.

-- ─── Members: the fields the registration wizard collects ──────────────────
ALTER TABLE "members"
  ADD COLUMN "title"                          TEXT,
  ADD COLUMN "middle_name"                    TEXT,
  ADD COLUMN "employer"                       TEXT,
  ADD COLUMN "preferred_contact"              TEXT,
  ADD COLUMN "join_method"                    TEXT,
  ADD COLUMN "previous_church"                TEXT,
  ADD COLUMN "invited_by"                     TEXT,
  ADD COLUMN "is_baptised"                    BOOLEAN,
  ADD COLUMN "baptism_date"                   DATE,
  ADD COLUMN "household_role"                 TEXT,
  ADD COLUMN "emergency_contact_name"         TEXT,
  ADD COLUMN "emergency_contact_phone"        TEXT,
  ADD COLUMN "emergency_contact_relationship" TEXT,
  ADD COLUMN "consent_data_processing_at"     TIMESTAMPTZ(6),
  ADD COLUMN "consent_given_by"               TEXT,
  ADD COLUMN "consent_communications"         BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN "directory_visible"              BOOLEAN NOT NULL DEFAULT true,
  ADD COLUMN "notes"                          TEXT;

ALTER TABLE "members" ADD CONSTRAINT "members_gender_check"
  CHECK ("gender" IS NULL OR "gender" IN ('male', 'female'));
ALTER TABLE "members" ADD CONSTRAINT "members_preferred_contact_check"
  CHECK ("preferred_contact" IS NULL OR "preferred_contact" IN ('phone', 'sms', 'whatsapp', 'email'));
ALTER TABLE "members" ADD CONSTRAINT "members_join_method_check"
  CHECK ("join_method" IS NULL OR "join_method" IN ('first_visit', 'transfer', 'baptism', 'profession_of_faith', 'born_into', 'other'));
ALTER TABLE "members" ADD CONSTRAINT "members_household_role_check"
  CHECK ("household_role" IS NULL OR "household_role" IN ('head', 'spouse', 'child', 'relative', 'other'));
ALTER TABLE "members" ADD CONSTRAINT "members_consent_given_by_check"
  CHECK ("consent_given_by" IS NULL OR "consent_given_by" IN ('self', 'guardian'));

-- One envelope number per church — the counting team credits giving by it.
CREATE UNIQUE INDEX "members_tenant_envelope_key"
  ON "members" ("tenant_id", "envelope_number")
  WHERE "envelope_number" IS NOT NULL;

-- Duplicate detection at registration looks people up by these.
CREATE INDEX "members_tenant_name_idx" ON "members" ("tenant_id", lower("first_name"), lower("last_name"));
CREATE INDEX "members_tenant_email_idx" ON "members" ("tenant_id", "email") WHERE "email" IS NOT NULL;

-- ─── Groups ────────────────────────────────────────────────────────────────
CREATE TABLE "groups" (
    "id"                 UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"          UUID NOT NULL,
    "name"               TEXT NOT NULL,
    "type"               TEXT NOT NULL DEFAULT 'ministry',
    "description"        TEXT,
    "unit_id"            UUID,
    "meeting_frequency"  TEXT NOT NULL DEFAULT 'weekly',
    "meeting_day"        TEXT,
    "meeting_time"       TEXT,
    "meeting_location"   TEXT,
    "is_open"            BOOLEAN NOT NULL DEFAULT true,
    "capacity"           INTEGER,
    "color"              TEXT NOT NULL DEFAULT '#4f46e5',
    "is_archived"        BOOLEAN NOT NULL DEFAULT false,
    "created_at"         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at"         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "groups_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "groups_type_check" CHECK ("type" IN ('ministry', 'small_group', 'choir', 'department', 'fellowship', 'class', 'committee', 'team')),
    CONSTRAINT "groups_frequency_check" CHECK ("meeting_frequency" IN ('weekly', 'fortnightly', 'monthly', 'irregular')),
    CONSTRAINT "groups_day_check" CHECK ("meeting_day" IS NULL OR "meeting_day" IN ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')),
    CONSTRAINT "groups_time_check" CHECK ("meeting_time" IS NULL OR "meeting_time" ~ '^[0-2][0-9]:[0-5][0-9]$'),
    CONSTRAINT "groups_capacity_check" CHECK ("capacity" IS NULL OR "capacity" > 0),
    CONSTRAINT "groups_color_check" CHECK ("color" ~ '^#[0-9a-fA-F]{6}$')
);

ALTER TABLE "groups" ADD CONSTRAINT "groups_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "groups" ADD CONSTRAINT "groups_unit_id_fkey" FOREIGN KEY ("unit_id") REFERENCES "hierarchy_units"("id") ON DELETE SET NULL;
CREATE INDEX "groups_tenant_id_idx" ON "groups" ("tenant_id");
-- Two live groups in one church can't share a name; an archived one frees it.
CREATE UNIQUE INDEX "groups_tenant_name_key" ON "groups" ("tenant_id", lower("name")) WHERE NOT "is_archived";

CREATE TABLE "group_memberships" (
    "id"         UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"  UUID NOT NULL,
    "group_id"   UUID NOT NULL,
    "member_id"  UUID NOT NULL,
    "role"       TEXT NOT NULL DEFAULT 'member',
    "note"       TEXT,
    "joined_at"  DATE NOT NULL DEFAULT CURRENT_DATE,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "group_memberships_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "group_memberships_role_check" CHECK ("role" IN ('leader', 'assistant', 'member'))
);

ALTER TABLE "group_memberships" ADD CONSTRAINT "group_memberships_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "group_memberships" ADD CONSTRAINT "group_memberships_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "groups"("id") ON DELETE CASCADE;
ALTER TABLE "group_memberships" ADD CONSTRAINT "group_memberships_member_id_fkey" FOREIGN KEY ("member_id") REFERENCES "members"("id") ON DELETE CASCADE;
CREATE UNIQUE INDEX "group_memberships_group_member_key" ON "group_memberships" ("group_id", "member_id");
CREATE INDEX "group_memberships_member_id_idx" ON "group_memberships" ("member_id");
CREATE INDEX "group_memberships_tenant_id_idx" ON "group_memberships" ("tenant_id");

-- A membership must join a group and a member of the SAME church. RLS already
-- hides other tenants' rows from reads, but a forged id in a write would still
-- satisfy a plain FK — this trigger closes that.
CREATE OR REPLACE FUNCTION group_membership_same_tenant() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM groups g WHERE g.id = NEW.group_id AND g.tenant_id = NEW.tenant_id)
     OR NOT EXISTS (SELECT 1 FROM members m WHERE m.id = NEW.member_id AND m.tenant_id = NEW.tenant_id) THEN
    RAISE EXCEPTION 'group membership crosses tenants' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER group_memberships_same_tenant
  BEFORE INSERT OR UPDATE ON "group_memberships"
  FOR EACH ROW EXECUTE FUNCTION group_membership_same_tenant();

-- ─── Row-level security ────────────────────────────────────────────────────
ALTER TABLE "groups" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "groups" USING (tenant_id = current_tenant_id());

ALTER TABLE "group_memberships" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "group_memberships" USING (tenant_id = current_tenant_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON "groups", "group_memberships" TO app_tenant, app_platform;

-- ─── Permissions for the system roles (mirrors src/hooks/useRole.ts) ───────
INSERT INTO "role_permissions" ("role_id", "permission") VALUES
  ('00000000-0000-0000-0000-000000000002', 'groups:read'),
  ('00000000-0000-0000-0000-000000000002', 'groups:manage'),
  ('00000000-0000-0000-0000-000000000003', 'groups:read'),
  ('00000000-0000-0000-0000-000000000003', 'groups:manage')
ON CONFLICT DO NOTHING;
