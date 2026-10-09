-- Prisma's own diff here also wanted to DROP every hand-written foreign key
-- and index from the previous migration — because they're invisible to its
-- model (this schema is deliberately relation-free, see schema.prisma's
-- header comment), a second `migrate dev --create-only` sees them as drift
-- to remove. That block has been stripped entirely; only genuinely new
-- tables/indexes from this schema change remain below, plus the
-- hand-written FKs/RLS/checks for them (same pattern as migration 0001).

-- CreateTable
CREATE TABLE "leadership_transfers" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "outgoing_leader_membership_id" UUID NOT NULL,
    "nominee_membership_id" UUID NOT NULL,
    "initiated_by_membership_id" UUID NOT NULL,
    "initiated_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "mfa_verified_at" TIMESTAMPTZ(6) NOT NULL,
    "required_approvals" INTEGER NOT NULL DEFAULT 2,
    "eligible_approver_ids" UUID[],
    "approvals" JSONB NOT NULL DEFAULT '[]',
    "status" TEXT NOT NULL DEFAULT 'pending_approvals',
    "completed_at" TIMESTAMPTZ(6),
    "canceled_at" TIMESTAMPTZ(6),

    CONSTRAINT "leadership_transfers_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "notification_preferences" (
    "user_id" UUID NOT NULL,
    "preferences" JSONB NOT NULL DEFAULT '{}',

    CONSTRAINT "notification_preferences_pkey" PRIMARY KEY ("user_id")
);

-- CreateTable
CREATE TABLE "feature_flag_overrides" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "feature_code" TEXT NOT NULL,
    "is_enabled" BOOLEAN NOT NULL,
    "overridden_by_user_id" UUID NOT NULL,
    "overridden_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "override_expires_at" TIMESTAMPTZ(6),
    "override_note" TEXT,

    CONSTRAINT "feature_flag_overrides_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "custom_domains" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "domain" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'pending_verification',
    "ssl_provisioned" BOOLEAN NOT NULL DEFAULT false,
    "verified_at" TIMESTAMPTZ(6),
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "custom_domains_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "data_erasure_requests" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "member_id" UUID NOT NULL,
    "requested_by_user_id" UUID NOT NULL,
    "requested_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "status" TEXT NOT NULL DEFAULT 'pending',
    "completed_at" TIMESTAMPTZ(6),
    "processed_by_user_id" UUID,
    "notes" TEXT,

    CONSTRAINT "data_erasure_requests_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "impersonation_sessions" (
    "id" UUID NOT NULL,
    "platform_admin_user_id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "impersonated_role" TEXT NOT NULL,
    "started_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "expires_at" TIMESTAMPTZ(6) NOT NULL,
    "ended_at" TIMESTAMPTZ(6),

    CONSTRAINT "impersonation_sessions_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "feature_flag_overrides_tenant_id_feature_code_key" ON "feature_flag_overrides"("tenant_id", "feature_code");

-- CreateIndex
CREATE UNIQUE INDEX "custom_domains_domain_key" ON "custom_domains"("domain");

-- ── Hand-written: FKs, RLS, checks for the new tables ────────────────────
ALTER TABLE "leadership_transfers" ADD CONSTRAINT "leadership_transfers_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "leadership_transfers" ADD CONSTRAINT "leadership_transfers_outgoing_fkey" FOREIGN KEY ("outgoing_leader_membership_id") REFERENCES "tenant_memberships"("id");
ALTER TABLE "leadership_transfers" ADD CONSTRAINT "leadership_transfers_nominee_fkey" FOREIGN KEY ("nominee_membership_id") REFERENCES "tenant_memberships"("id");
ALTER TABLE "leadership_transfers" ADD CONSTRAINT "leadership_transfers_initiated_by_fkey" FOREIGN KEY ("initiated_by_membership_id") REFERENCES "tenant_memberships"("id");
ALTER TABLE "leadership_transfers" ADD CONSTRAINT "leadership_transfers_status_check" CHECK ("status" IN ('pending_approvals','completed','canceled'));
CREATE INDEX "leadership_transfers_tenant_idx" ON "leadership_transfers" ("tenant_id");

ALTER TABLE "notification_preferences" ADD CONSTRAINT "notification_preferences_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE;

ALTER TABLE "feature_flag_overrides" ADD CONSTRAINT "feature_flag_overrides_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "feature_flag_overrides" ADD CONSTRAINT "feature_flag_overrides_overridden_by_fkey" FOREIGN KEY ("overridden_by_user_id") REFERENCES "users"("id");

ALTER TABLE "custom_domains" ADD CONSTRAINT "custom_domains_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "custom_domains" ADD CONSTRAINT "custom_domains_status_check" CHECK ("status" IN ('pending_verification','active','failed','suspended'));
CREATE INDEX "custom_domains_tenant_idx" ON "custom_domains" ("tenant_id");

ALTER TABLE "data_erasure_requests" ADD CONSTRAINT "data_erasure_requests_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "data_erasure_requests" ADD CONSTRAINT "data_erasure_requests_member_id_fkey" FOREIGN KEY ("member_id") REFERENCES "members"("id");
ALTER TABLE "data_erasure_requests" ADD CONSTRAINT "data_erasure_requests_requested_by_fkey" FOREIGN KEY ("requested_by_user_id") REFERENCES "users"("id");
ALTER TABLE "data_erasure_requests" ADD CONSTRAINT "data_erasure_requests_processed_by_fkey" FOREIGN KEY ("processed_by_user_id") REFERENCES "users"("id");
ALTER TABLE "data_erasure_requests" ADD CONSTRAINT "data_erasure_requests_status_check" CHECK ("status" IN ('pending','approved','processing','completed','rejected'));
CREATE INDEX "data_erasure_requests_tenant_idx" ON "data_erasure_requests" ("tenant_id");

-- impersonation_sessions references organizations (tenant being impersonated
-- into) but the acting admin is platform-level, not tenant-scoped — no FK to
-- tenant_memberships/roles, just a plain users.id for the admin.
ALTER TABLE "impersonation_sessions" ADD CONSTRAINT "impersonation_sessions_admin_fkey" FOREIGN KEY ("platform_admin_user_id") REFERENCES "users"("id");
ALTER TABLE "impersonation_sessions" ADD CONSTRAINT "impersonation_sessions_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;

-- RLS: leadership_transfers, notification_preferences, feature_flag_overrides,
-- custom_domains, data_erasure_requests are ordinary tenant-scoped tables
-- (tenant_id NOT NULL except notification_preferences, which is keyed by
-- user_id instead and is global like `users` — no tenant_id, no RLS, same
-- trust model). impersonation_sessions is platform-only: app_tenant never
-- touches it, only app_platform (bypassrls), so no tenant_isolation policy
-- is needed — but it's enabled with no policy so a future app_tenant grant
-- mistake fails closed instead of open.
ALTER TABLE "leadership_transfers" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "leadership_transfers" USING (tenant_id = current_tenant_id());

ALTER TABLE "feature_flag_overrides" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "feature_flag_overrides" USING (tenant_id = current_tenant_id());

ALTER TABLE "custom_domains" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "custom_domains" USING (tenant_id = current_tenant_id());

ALTER TABLE "data_erasure_requests" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "data_erasure_requests" USING (tenant_id = current_tenant_id());

ALTER TABLE "impersonation_sessions" ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON "leadership_transfers", "notification_preferences", "feature_flag_overrides", "custom_domains", "data_erasure_requests", "impersonation_sessions" TO app_tenant, app_platform;
