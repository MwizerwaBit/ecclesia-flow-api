-- Organisation lifecycle, billing, leadership structure, ID numbers,
-- groups v2 and events v2.
--
-- Conventions (same as every hand-written migration here): every new table
-- that holds a church's data has a NOT NULL tenant_id, a FK to organizations,
-- RLS enabled with the tenant_isolation policy, and grants for the two app
-- roles. Webhook/billing writes run on app_platform (BYPASSRLS) because they
-- arrive with no tenant session at all.

-- ═══════════════════════════ Organisations ═══════════════════════════
ALTER TABLE "organizations"
  ALTER COLUMN "status" SET DEFAULT 'pending',
  ADD COLUMN "verification_status" TEXT NOT NULL DEFAULT 'unverified',
  ADD COLUMN "registration_number" TEXT,
  ADD COLUMN "denomination"        TEXT,
  ADD COLUMN "contact_email"       TEXT,
  ADD COLUMN "contact_phone"       TEXT,
  ADD COLUMN "address_line1"       TEXT,
  ADD COLUMN "city"                TEXT,
  ADD COLUMN "region"              TEXT,
  ADD COLUMN "website"             TEXT,
  ADD COLUMN "activated_at"        TIMESTAMPTZ(6),
  ADD COLUMN "activation_source"   TEXT,
  ADD COLUMN "activation_note"     TEXT;

ALTER TABLE "organizations" DROP CONSTRAINT "organizations_status_check";
ALTER TABLE "organizations" ADD CONSTRAINT "organizations_status_check"
  CHECK ("status" IN ('pending', 'trial', 'active', 'suspended', 'canceled'));
ALTER TABLE "organizations" ADD CONSTRAINT "organizations_verification_check"
  CHECK ("verification_status" IN ('unverified', 'pending_review', 'verified', 'rejected'));
ALTER TABLE "organizations" ADD CONSTRAINT "organizations_activation_source_check"
  CHECK ("activation_source" IS NULL OR "activation_source" IN ('payment', 'platform_admin'));

-- Government registration certificate (and any later official documents).
-- Files live outside the public static mount; only a hash-checked,
-- permission-checked download endpoint serves them.
CREATE TABLE "org_documents" (
    "id"                  UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"           UUID NOT NULL,
    "kind"                TEXT NOT NULL DEFAULT 'government_certificate',
    "file_name"           TEXT NOT NULL,
    "mime_type"           TEXT NOT NULL,
    "size_bytes"          INTEGER NOT NULL,
    "sha256"              TEXT NOT NULL,
    "storage_key"         TEXT NOT NULL,
    "status"              TEXT NOT NULL DEFAULT 'pending_review',
    "uploaded_by_user_id" UUID NOT NULL,
    "uploaded_at"         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "reviewed_by_user_id" UUID,
    "reviewed_at"         TIMESTAMPTZ(6),
    "review_note"         TEXT,
    CONSTRAINT "org_documents_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "org_documents_kind_check" CHECK ("kind" IN ('government_certificate')),
    CONSTRAINT "org_documents_status_check" CHECK ("status" IN ('pending_review', 'verified', 'rejected', 'superseded')),
    CONSTRAINT "org_documents_size_check" CHECK ("size_bytes" > 0 AND "size_bytes" <= 10485760)
);
ALTER TABLE "org_documents" ADD CONSTRAINT "org_documents_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "org_documents" ADD CONSTRAINT "org_documents_uploader_fkey" FOREIGN KEY ("uploaded_by_user_id") REFERENCES "users"("id");
CREATE INDEX "org_documents_tenant_idx" ON "org_documents" ("tenant_id", "kind", "uploaded_at" DESC);

-- Per-church module switches on top of the plan's defaults.
CREATE TABLE "org_module_overrides" (
    "tenant_id"       UUID NOT NULL,
    "module_id"       TEXT NOT NULL,
    "enabled"         BOOLEAN NOT NULL,
    "set_by_user_id"  UUID NOT NULL,
    "set_by_platform" BOOLEAN NOT NULL DEFAULT false,
    "updated_at"      TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "org_module_overrides_pkey" PRIMARY KEY ("tenant_id", "module_id")
);
ALTER TABLE "org_module_overrides" ADD CONSTRAINT "org_module_overrides_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;

-- ═══════════════════════════ Billing ═══════════════════════════
CREATE TABLE "billing_subscriptions" (
    "id"                       UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"                UUID NOT NULL,
    "provider"                 TEXT NOT NULL,
    "tier"                     TEXT NOT NULL,
    "interval"                 TEXT NOT NULL,
    "status"                   TEXT NOT NULL DEFAULT 'incomplete',
    "provider_customer_id"     TEXT,
    "provider_subscription_id" TEXT,
    "current_period_end"       TIMESTAMPTZ(6),
    "created_at"               TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at"               TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "billing_subscriptions_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "billing_subscriptions_tenant_key" UNIQUE ("tenant_id"),
    CONSTRAINT "billing_subscriptions_interval_check" CHECK ("interval" IN ('month', 'year')),
    CONSTRAINT "billing_subscriptions_status_check" CHECK ("status" IN ('incomplete', 'active', 'past_due', 'canceled'))
);
ALTER TABLE "billing_subscriptions" ADD CONSTRAINT "billing_subscriptions_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;

CREATE TABLE "billing_checkout_sessions" (
    "id"                  UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"           UUID NOT NULL,
    "provider"            TEXT NOT NULL,
    "provider_session_id" TEXT NOT NULL,
    "tier"                TEXT NOT NULL,
    "interval"            TEXT NOT NULL,
    "amount_cents"        INTEGER NOT NULL,
    "currency"            TEXT NOT NULL,
    "status"              TEXT NOT NULL DEFAULT 'open',
    "created_by_user_id"  UUID NOT NULL,
    "created_at"          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completed_at"        TIMESTAMPTZ(6),
    CONSTRAINT "billing_checkout_sessions_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "billing_checkout_sessions_provider_key" UNIQUE ("provider", "provider_session_id"),
    CONSTRAINT "billing_checkout_sessions_status_check" CHECK ("status" IN ('open', 'completed', 'expired'))
);
ALTER TABLE "billing_checkout_sessions" ADD CONSTRAINT "billing_checkout_sessions_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;

CREATE TABLE "billing_payments" (
    "id"                 UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"          UUID NOT NULL,
    "provider"           TEXT NOT NULL,
    "provider_reference" TEXT NOT NULL,
    "amount_cents"       INTEGER NOT NULL,
    "currency"           TEXT NOT NULL,
    "status"             TEXT NOT NULL,
    "description"        TEXT,
    "paid_at"            TIMESTAMPTZ(6),
    "created_at"         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "billing_payments_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "billing_payments_reference_key" UNIQUE ("provider", "provider_reference")
);
ALTER TABLE "billing_payments" ADD CONSTRAINT "billing_payments_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;

-- Every provider webhook ever accepted. The unique event id is the
-- idempotency key: a replayed or retried webhook is recorded once, acted on once.
CREATE TABLE "billing_webhook_events" (
    "id"                UUID NOT NULL DEFAULT gen_random_uuid(),
    "provider"          TEXT NOT NULL,
    "provider_event_id" TEXT NOT NULL,
    "type"              TEXT NOT NULL,
    "tenant_id"         UUID,
    "payload"           JSONB NOT NULL,
    "processed_at"      TIMESTAMPTZ(6),
    "created_at"        TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "billing_webhook_events_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "billing_webhook_events_event_key" UNIQUE ("provider", "provider_event_id")
);

-- ═══════════════════════════ Leadership ═══════════════════════════
-- The church's own leadership tree, in its own words. A position may grant an
-- RBAC role and a branch scope to whoever holds it.
CREATE TABLE "leadership_positions" (
    "id"          UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"   UUID NOT NULL,
    "parent_id"   UUID,
    "title"       TEXT NOT NULL,
    "description" TEXT,
    "role_id"     UUID,
    "unit_id"     UUID,
    "sort_order"  INTEGER NOT NULL DEFAULT 0,
    "created_at"  TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "leadership_positions_pkey" PRIMARY KEY ("id")
);
ALTER TABLE "leadership_positions" ADD CONSTRAINT "leadership_positions_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "leadership_positions" ADD CONSTRAINT "leadership_positions_parent_fkey" FOREIGN KEY ("parent_id") REFERENCES "leadership_positions"("id") ON DELETE RESTRICT;
ALTER TABLE "leadership_positions" ADD CONSTRAINT "leadership_positions_role_fkey" FOREIGN KEY ("role_id") REFERENCES "roles"("id") ON DELETE SET NULL;
ALTER TABLE "leadership_positions" ADD CONSTRAINT "leadership_positions_unit_fkey" FOREIGN KEY ("unit_id") REFERENCES "hierarchy_units"("id") ON DELETE SET NULL;
CREATE INDEX "leadership_positions_tenant_idx" ON "leadership_positions" ("tenant_id", "parent_id", "sort_order");

CREATE TABLE "leadership_assignments" (
    "id"                  UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"           UUID NOT NULL,
    "position_id"         UUID NOT NULL,
    "membership_id"       UUID NOT NULL,
    "assigned_by_user_id" UUID NOT NULL,
    "assigned_at"         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "leadership_assignments_pkey" PRIMARY KEY ("id"),
    -- One position per person keeps "what can this person do" unambiguous.
    CONSTRAINT "leadership_assignments_membership_key" UNIQUE ("membership_id")
);
ALTER TABLE "leadership_assignments" ADD CONSTRAINT "leadership_assignments_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "leadership_assignments" ADD CONSTRAINT "leadership_assignments_position_fkey" FOREIGN KEY ("position_id") REFERENCES "leadership_positions"("id") ON DELETE CASCADE;
ALTER TABLE "leadership_assignments" ADD CONSTRAINT "leadership_assignments_membership_fkey" FOREIGN KEY ("membership_id") REFERENCES "tenant_memberships"("id") ON DELETE CASCADE;

-- ═══════════════════════════ People: ID numbers ═══════════════════════════
-- Never stored in clear: an encrypted copy (for authorised display/export),
-- a keyed hash (for matching and uniqueness) and the last four characters.
ALTER TABLE "members"
  ADD COLUMN "id_type"               TEXT,
  ADD COLUMN "national_id_hash"      TEXT,
  ADD COLUMN "national_id_encrypted" TEXT,
  ADD COLUMN "national_id_last4"     TEXT;
ALTER TABLE "members" ADD CONSTRAINT "members_id_type_check"
  CHECK ("id_type" IS NULL OR "id_type" IN ('national_id', 'passport', 'other'));
CREATE UNIQUE INDEX "members_tenant_national_id_key"
  ON "members" ("tenant_id", "national_id_hash") WHERE "national_id_hash" IS NOT NULL;
CREATE INDEX "members_user_id_idx" ON "members" ("user_id") WHERE "user_id" IS NOT NULL;

-- ═══════════════════════════ Groups v2 ═══════════════════════════
CREATE TABLE "group_roles" (
    "id"           UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"    UUID NOT NULL,
    "key"          TEXT NOT NULL,
    "name"         TEXT NOT NULL,
    "capabilities" TEXT[] NOT NULL DEFAULT '{}',
    "rank"         INTEGER NOT NULL DEFAULT 100,
    "is_system"    BOOLEAN NOT NULL DEFAULT false,
    "created_at"   TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "group_roles_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "group_roles_tenant_key_key" UNIQUE ("tenant_id", "key"),
    CONSTRAINT "group_roles_key_check" CHECK ("key" ~ '^[a-z][a-z0-9_]{1,39}$'),
    CONSTRAINT "group_roles_capabilities_check"
      CHECK ("capabilities" <@ ARRAY['manage_roster', 'edit_group', 'message', 'manage_events']::TEXT[])
);
ALTER TABLE "group_roles" ADD CONSTRAINT "group_roles_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;

-- Every existing church gets the three built-in group roles.
INSERT INTO "group_roles" ("tenant_id", "key", "name", "capabilities", "rank", "is_system")
SELECT o.id, r.key, r.name, r.caps, r.rank, true
FROM "organizations" o
CROSS JOIN (VALUES
  ('leader', 'Leader', ARRAY['manage_roster', 'edit_group', 'message', 'manage_events']::TEXT[], 0),
  ('assistant', 'Assistant', ARRAY['manage_roster', 'message']::TEXT[], 10),
  ('member', 'Member', ARRAY[]::TEXT[], 100)
) AS r(key, name, caps, rank)
ON CONFLICT DO NOTHING;

ALTER TABLE "group_memberships" DROP CONSTRAINT "group_memberships_role_check";
ALTER TABLE "group_memberships"
  ADD COLUMN "status"             TEXT NOT NULL DEFAULT 'active',
  ADD COLUMN "invited_by_user_id" UUID,
  ADD COLUMN "invited_at"         TIMESTAMPTZ(6),
  ADD COLUMN "responded_at"       TIMESTAMPTZ(6);
ALTER TABLE "group_memberships" ADD CONSTRAINT "group_memberships_status_check"
  CHECK ("status" IN ('invited', 'active', 'declined'));
-- The role must be one this church has defined.
ALTER TABLE "group_memberships" ADD CONSTRAINT "group_memberships_role_fkey"
  FOREIGN KEY ("tenant_id", "role") REFERENCES "group_roles"("tenant_id", "key") ON UPDATE CASCADE;

-- ═══════════════════════════ Events v2 ═══════════════════════════
ALTER TABLE "events"
  ADD COLUMN "visibility"      TEXT NOT NULL DEFAULT 'members',
  ADD COLUMN "group_id"        UUID,
  ADD COLUMN "cover_image_url" TEXT,
  ADD COLUMN "theme"           JSONB NOT NULL DEFAULT '{}',
  ADD COLUMN "online_url"      TEXT,
  ADD COLUMN "capacity"        INTEGER,
  ADD COLUMN "submitted_at"    TIMESTAMPTZ(6),
  ADD COLUMN "published_at"    TIMESTAMPTZ(6);
UPDATE "events" SET "visibility" = 'public' WHERE "is_public";
UPDATE "events" SET "status" = 'published' WHERE "status" NOT IN ('draft', 'pending_review', 'changes_requested', 'published', 'canceled', 'completed');
ALTER TABLE "events" ADD CONSTRAINT "events_visibility_check" CHECK ("visibility" IN ('public', 'members', 'private'));
ALTER TABLE "events" DROP CONSTRAINT "events_status_check";
ALTER TABLE "events" ADD CONSTRAINT "events_status_check"
  CHECK ("status" IN ('draft', 'pending_review', 'changes_requested', 'published', 'canceled', 'completed'));
ALTER TABLE "events" ADD CONSTRAINT "events_capacity_check" CHECK ("capacity" IS NULL OR "capacity" > 0);
ALTER TABLE "events" ADD CONSTRAINT "events_group_fkey" FOREIGN KEY ("group_id") REFERENCES "groups"("id") ON DELETE SET NULL;

CREATE TABLE "event_reviews" (
    "id"               UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"        UUID NOT NULL,
    "event_id"         UUID NOT NULL,
    "reviewer_user_id" UUID NOT NULL,
    "status"           TEXT NOT NULL DEFAULT 'pending',
    "comment"          TEXT,
    "decided_at"       TIMESTAMPTZ(6),
    "created_at"       TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "event_reviews_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "event_reviews_event_reviewer_key" UNIQUE ("event_id", "reviewer_user_id"),
    CONSTRAINT "event_reviews_status_check" CHECK ("status" IN ('pending', 'approved', 'changes_requested'))
);
ALTER TABLE "event_reviews" ADD CONSTRAINT "event_reviews_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "event_reviews" ADD CONSTRAINT "event_reviews_event_fkey" FOREIGN KEY ("event_id") REFERENCES "events"("id") ON DELETE CASCADE;
ALTER TABLE "event_reviews" ADD CONSTRAINT "event_reviews_reviewer_fkey" FOREIGN KEY ("reviewer_user_id") REFERENCES "users"("id");

-- ═══════════════════════════ Roles ═══════════════════════════
-- Fourth system role: the church leader, above the administrator.
INSERT INTO "roles" ("id", "tenant_id", "name", "color", "is_system")
VALUES ('00000000-0000-0000-0000-000000000004', NULL, 'leader', '#7c3aed', true)
ON CONFLICT DO NOTHING;

-- ═══════════════════════════ Row-level security ═══════════════════════════
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'org_documents', 'org_module_overrides', 'billing_subscriptions', 'billing_checkout_sessions',
    'billing_payments', 'leadership_positions', 'leadership_assignments', 'group_roles', 'event_reviews'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_tenant_id())', t);
  END LOOP;
END $$;
-- Webhook events have no tenant session; only the platform role touches them.
ALTER TABLE "billing_webhook_events" ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON
  "org_documents", "org_module_overrides", "billing_subscriptions", "billing_checkout_sessions",
  "billing_payments", "leadership_positions", "leadership_assignments", "group_roles", "event_reviews"
TO app_tenant, app_platform;
GRANT SELECT, INSERT, UPDATE ON "billing_webhook_events" TO app_platform;
