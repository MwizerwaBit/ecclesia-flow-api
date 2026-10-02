-- Extensions (gen_random_uuid in case raw SQL ever needs it; citext for
-- case-insensitive email columns, used above by Prisma's own CREATE TABLE).
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

-- CreateTable
CREATE TABLE "organizations" (
    "id" UUID NOT NULL,
    "legal_name" TEXT NOT NULL,
    "display_name" TEXT NOT NULL,
    "slug" TEXT NOT NULL,
    "country" CHAR(2) NOT NULL,
    "currency" CHAR(3) NOT NULL,
    "timezone" TEXT NOT NULL,
    "language" TEXT NOT NULL DEFAULT 'en',
    "status" TEXT NOT NULL DEFAULT 'trial',
    "tier" TEXT NOT NULL DEFAULT 'seed',
    "logo_url" TEXT,
    "primary_color" TEXT,
    "custom_domain" TEXT,
    "trial_ends_at" TIMESTAMPTZ(6),
    "renewal_date" TIMESTAMPTZ(6),
    "storage_used_mb" INTEGER NOT NULL DEFAULT 0,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "organizations_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "users" (
    "id" UUID NOT NULL,
    "email" CITEXT NOT NULL,
    "password_hash" TEXT,
    "first_name" TEXT NOT NULL,
    "last_name" TEXT NOT NULL,
    "photo_url" TEXT,
    "is_platform_admin" BOOLEAN NOT NULL DEFAULT false,
    "platform_admin_level" TEXT,
    "mfa_enabled" BOOLEAN NOT NULL DEFAULT false,
    "mfa_secret" TEXT,
    "mfa_backup_codes" TEXT[],
    "status" TEXT NOT NULL DEFAULT 'active',
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "last_login_at" TIMESTAMPTZ(6),

    CONSTRAINT "users_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "tenant_memberships" (
    "id" UUID NOT NULL,
    "user_id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "role_id" UUID NOT NULL,
    "unit_scope_id" UUID,
    "status" TEXT NOT NULL DEFAULT 'active',
    "is_primary" BOOLEAN NOT NULL DEFAULT false,
    "is_leader" BOOLEAN NOT NULL DEFAULT false,
    "invited_at" TIMESTAMPTZ(6),
    "accepted_at" TIMESTAMPTZ(6),
    "last_active_at" TIMESTAMPTZ(6),
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "tenant_memberships_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "refresh_tokens" (
    "id" UUID NOT NULL,
    "user_id" UUID NOT NULL,
    "membership_id" UUID,
    "token_hash" TEXT NOT NULL,
    "family_id" UUID NOT NULL,
    "revoked_at" TIMESTAMPTZ(6),
    "replaced_by_id" UUID,
    "user_agent" TEXT,
    "ip_address" TEXT,
    "expires_at" TIMESTAMPTZ(6) NOT NULL,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "refresh_tokens_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "roles" (
    "id" UUID NOT NULL,
    "tenant_id" UUID,
    "name" TEXT NOT NULL,
    "color" TEXT,
    "is_system" BOOLEAN NOT NULL DEFAULT false,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "roles_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "role_permissions" (
    "role_id" UUID NOT NULL,
    "permission" TEXT NOT NULL,

    CONSTRAINT "role_permissions_pkey" PRIMARY KEY ("role_id","permission")
);

-- CreateTable
CREATE TABLE "hierarchy_units" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "parent_id" UUID,
    "name" TEXT NOT NULL,
    "type" TEXT NOT NULL,
    "code" TEXT,
    "address" TEXT,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "hierarchy_units_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "hierarchy_closure" (
    "ancestor_id" UUID NOT NULL,
    "descendant_id" UUID NOT NULL,
    "depth" INTEGER NOT NULL,

    CONSTRAINT "hierarchy_closure_pkey" PRIMARY KEY ("ancestor_id","descendant_id")
);

-- CreateTable
CREATE TABLE "households" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "name" TEXT NOT NULL,
    "head_member_id" UUID,
    "address_line1" TEXT,
    "address_line2" TEXT,
    "city" TEXT,
    "state" TEXT,
    "country" TEXT,
    "postal_code" TEXT,
    "total_giving" DECIMAL(12,2) NOT NULL DEFAULT 0,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "households_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "members" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "user_id" UUID,
    "household_id" UUID,
    "first_name" TEXT NOT NULL,
    "last_name" TEXT NOT NULL,
    "preferred_name" TEXT,
    "photo_url" TEXT,
    "email" CITEXT,
    "phone" TEXT,
    "whatsapp" TEXT,
    "status" TEXT NOT NULL DEFAULT 'active',
    "envelope_number" TEXT,
    "unit_id" UUID,
    "joined_at" DATE,
    "last_seen_at" DATE,
    "date_of_birth" DATE,
    "gender" TEXT,
    "marital_status" TEXT,
    "occupation" TEXT,
    "address_line1" TEXT,
    "address_line2" TEXT,
    "city" TEXT,
    "state" TEXT,
    "country" TEXT,
    "postal_code" TEXT,
    "transferred_to_member_id" UUID,
    "transferred_from_member_id" UUID,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "members_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "sacramental_records" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "member_id" UUID NOT NULL,
    "type" TEXT NOT NULL,
    "date" DATE NOT NULL,
    "officiant_name" TEXT,
    "location" TEXT,
    "notes" TEXT,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "sacramental_records_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "pastoral_notes" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "member_id" UUID NOT NULL,
    "content" TEXT NOT NULL,
    "author_user_id" UUID NOT NULL,
    "is_private" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "pastoral_notes_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "member_transfers" (
    "id" UUID NOT NULL,
    "from_tenant_id" UUID NOT NULL,
    "to_tenant_id" UUID NOT NULL,
    "from_member_id" UUID NOT NULL,
    "to_member_id" UUID,
    "requested_by_user_id" UUID NOT NULL,
    "approved_by_user_id" UUID,
    "status" TEXT NOT NULL DEFAULT 'pending',
    "history_scope" TEXT NOT NULL DEFAULT 'identity_only',
    "certificate_id" UUID,
    "notes" TEXT,
    "requested_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "decided_at" TIMESTAMPTZ(6),
    "completed_at" TIMESTAMPTZ(6),

    CONSTRAINT "member_transfers_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "events" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "title" TEXT NOT NULL,
    "type" TEXT NOT NULL,
    "description" TEXT,
    "location" TEXT,
    "start_date_time" TIMESTAMPTZ(6) NOT NULL,
    "end_date_time" TIMESTAMPTZ(6),
    "is_recurring" BOOLEAN NOT NULL DEFAULT false,
    "recurrence_rule" TEXT,
    "status" TEXT NOT NULL DEFAULT 'draft',
    "unit_id" UUID,
    "attendance_mode" TEXT NOT NULL DEFAULT 'individual',
    "is_public" BOOLEAN NOT NULL DEFAULT false,
    "created_by_user_id" UUID NOT NULL,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "events_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "attendance_records" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "event_id" UUID NOT NULL,
    "mode" TEXT NOT NULL,
    "member_id" UUID,
    "adult_count" INTEGER,
    "child_count" INTEGER,
    "total_count" INTEGER,
    "marked_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "marked_by_user_id" UUID NOT NULL,

    CONSTRAINT "attendance_records_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "funds" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "name" TEXT NOT NULL,
    "description" TEXT,
    "is_default" BOOLEAN NOT NULL DEFAULT false,
    "target" DECIMAL(12,2),
    "total_received" DECIMAL(12,2) NOT NULL DEFAULT 0,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "funds_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "donation_batches" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "name" TEXT NOT NULL,
    "description" TEXT,
    "service_id" UUID,
    "date" DATE NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'open',
    "verified_total" DECIMAL(12,2),
    "created_by_user_id" UUID NOT NULL,
    "closed_at" TIMESTAMPTZ(6),
    "posted_at" TIMESTAMPTZ(6),
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "donation_batches_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "donations" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "batch_id" UUID NOT NULL,
    "member_id" UUID,
    "is_guest" BOOLEAN NOT NULL DEFAULT false,
    "guest_name" TEXT,
    "envelope_number" TEXT,
    "fund_id" UUID NOT NULL,
    "amount" DECIMAL(12,2) NOT NULL,
    "payment_method" TEXT NOT NULL,
    "notes" TEXT,
    "reference_number" TEXT,
    "is_voided" BOOLEAN NOT NULL DEFAULT false,
    "voided_at" TIMESTAMPTZ(6),
    "void_reason" TEXT,
    "voided_by_user_id" UUID,
    "created_by_user_id" UUID NOT NULL,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "donations_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "pledges" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "member_id" UUID NOT NULL,
    "fund_id" UUID NOT NULL,
    "pledge_amount" DECIMAL(12,2) NOT NULL,
    "amount_fulfilled" DECIMAL(12,2) NOT NULL DEFAULT 0,
    "start_date" DATE NOT NULL,
    "end_date" DATE NOT NULL,
    "frequency" TEXT,
    "status" TEXT NOT NULL DEFAULT 'active',
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "pledges_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "announcements" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "title" TEXT NOT NULL,
    "body" TEXT NOT NULL,
    "is_pinned" BOOLEAN NOT NULL DEFAULT false,
    "status" TEXT NOT NULL DEFAULT 'draft',
    "channels" TEXT[],
    "audience_filter" JSONB,
    "scheduled_at" TIMESTAMPTZ(6),
    "sent_at" TIMESTAMPTZ(6),
    "author_user_id" UUID NOT NULL,
    "sent_count" INTEGER,
    "delivered_count" INTEGER,
    "opened_count" INTEGER,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "announcements_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "message_templates" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "name" TEXT NOT NULL,
    "subject" TEXT,
    "body" TEXT NOT NULL,
    "category" TEXT,
    "is_active" BOOLEAN NOT NULL DEFAULT true,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "message_templates_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "notifications" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "user_id" UUID NOT NULL,
    "type" TEXT NOT NULL,
    "title" TEXT NOT NULL,
    "body" TEXT NOT NULL,
    "is_read" BOOLEAN NOT NULL DEFAULT false,
    "action_url" TEXT,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "notifications_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "certificate_templates" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "name" TEXT NOT NULL,
    "category" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'draft',
    "background_image_url" TEXT,
    "tokens" JSONB NOT NULL DEFAULT '[]',
    "qr_code_position" JSONB,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "certificate_templates_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "certificates" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "template_id" UUID NOT NULL,
    "member_id" UUID NOT NULL,
    "issued_by_user_id" UUID NOT NULL,
    "issued_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "serial_number" TEXT NOT NULL,
    "qr_hash" TEXT NOT NULL,
    "custom_values" JSONB NOT NULL DEFAULT '{}',
    "is_revoked" BOOLEAN NOT NULL DEFAULT false,
    "revoked_at" TIMESTAMPTZ(6),
    "revoked_reason" TEXT,
    "pdf_url" TEXT,

    CONSTRAINT "certificates_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "media_assets" (
    "id" UUID NOT NULL,
    "tenant_id" UUID NOT NULL,
    "uploaded_by_user_id" UUID NOT NULL,
    "kind" TEXT NOT NULL,
    "storage_key" TEXT NOT NULL,
    "url" TEXT NOT NULL,
    "mime_type" TEXT NOT NULL,
    "size_bytes" BIGINT NOT NULL,
    "width" INTEGER,
    "height" INTEGER,
    "alt_text" TEXT,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "media_assets_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "media_attachments" (
    "media_id" UUID NOT NULL,
    "attachable_type" TEXT NOT NULL,
    "attachable_id" UUID NOT NULL,
    "role" TEXT NOT NULL DEFAULT 'primary',
    "position" INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT "media_attachments_pkey" PRIMARY KEY ("media_id","attachable_type","attachable_id","role")
);

-- CreateTable
CREATE TABLE "audit_logs" (
    "id" UUID NOT NULL,
    "tenant_id" UUID,
    "actor_user_id" UUID,
    "action" TEXT NOT NULL,
    "resource_type" TEXT NOT NULL,
    "resource_id" UUID,
    "metadata" JSONB,
    "ip_address" INET,
    "is_impersonated" BOOLEAN NOT NULL DEFAULT false,
    "impersonated_by_user_id" UUID,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "audit_logs_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "organizations_slug_key" ON "organizations"("slug");

-- CreateIndex
CREATE UNIQUE INDEX "organizations_custom_domain_key" ON "organizations"("custom_domain");

-- CreateIndex
CREATE UNIQUE INDEX "users_email_key" ON "users"("email");

-- CreateIndex
CREATE UNIQUE INDEX "tenant_memberships_user_id_tenant_id_key" ON "tenant_memberships"("user_id", "tenant_id");

-- CreateIndex
CREATE UNIQUE INDEX "refresh_tokens_token_hash_key" ON "refresh_tokens"("token_hash");

-- CreateIndex
CREATE UNIQUE INDEX "roles_tenant_id_name_key" ON "roles"("tenant_id", "name");

-- CreateIndex
CREATE UNIQUE INDEX "attendance_records_event_id_member_id_key" ON "attendance_records"("event_id", "member_id");

-- CreateIndex
CREATE UNIQUE INDEX "certificates_qr_hash_key" ON "certificates"("qr_hash");

-- CreateIndex
CREATE UNIQUE INDEX "certificates_tenant_id_serial_number_key" ON "certificates"("tenant_id", "serial_number");

-- ═══════════════════════════════════════════════════════════════════════
-- Everything below is hand-written, not Prisma-generated — Prisma only
-- knows about the columns it was told to model (deliberately relation-free,
-- see prisma/schema.prisma's header comment). Foreign keys, RLS, the
-- closure-table trigger, the member-transfer function, and seed data all
-- live here instead.
-- ═══════════════════════════════════════════════════════════════════════

-- ── Foreign keys ─────────────────────────────────────────────────────────
ALTER TABLE "tenant_memberships" ADD CONSTRAINT "tenant_memberships_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE;
ALTER TABLE "tenant_memberships" ADD CONSTRAINT "tenant_memberships_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "tenant_memberships" ADD CONSTRAINT "tenant_memberships_role_id_fkey" FOREIGN KEY ("role_id") REFERENCES "roles"("id");
ALTER TABLE "tenant_memberships" ADD CONSTRAINT "tenant_memberships_unit_scope_id_fkey" FOREIGN KEY ("unit_scope_id") REFERENCES "hierarchy_units"("id") ON DELETE SET NULL;

ALTER TABLE "refresh_tokens" ADD CONSTRAINT "refresh_tokens_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE;
ALTER TABLE "refresh_tokens" ADD CONSTRAINT "refresh_tokens_membership_id_fkey" FOREIGN KEY ("membership_id") REFERENCES "tenant_memberships"("id") ON DELETE CASCADE;

ALTER TABLE "roles" ADD CONSTRAINT "roles_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "role_permissions" ADD CONSTRAINT "role_permissions_role_id_fkey" FOREIGN KEY ("role_id") REFERENCES "roles"("id") ON DELETE CASCADE;

ALTER TABLE "hierarchy_units" ADD CONSTRAINT "hierarchy_units_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "hierarchy_units" ADD CONSTRAINT "hierarchy_units_parent_id_fkey" FOREIGN KEY ("parent_id") REFERENCES "hierarchy_units"("id") ON DELETE SET NULL;
ALTER TABLE "hierarchy_closure" ADD CONSTRAINT "hierarchy_closure_ancestor_id_fkey" FOREIGN KEY ("ancestor_id") REFERENCES "hierarchy_units"("id") ON DELETE CASCADE;
ALTER TABLE "hierarchy_closure" ADD CONSTRAINT "hierarchy_closure_descendant_id_fkey" FOREIGN KEY ("descendant_id") REFERENCES "hierarchy_units"("id") ON DELETE CASCADE;

ALTER TABLE "households" ADD CONSTRAINT "households_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;

ALTER TABLE "members" ADD CONSTRAINT "members_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "members" ADD CONSTRAINT "members_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE SET NULL;
ALTER TABLE "members" ADD CONSTRAINT "members_household_id_fkey" FOREIGN KEY ("household_id") REFERENCES "households"("id") ON DELETE SET NULL;
ALTER TABLE "members" ADD CONSTRAINT "members_unit_id_fkey" FOREIGN KEY ("unit_id") REFERENCES "hierarchy_units"("id") ON DELETE SET NULL;
ALTER TABLE "members" ADD CONSTRAINT "members_transferred_to_member_id_fkey" FOREIGN KEY ("transferred_to_member_id") REFERENCES "members"("id");
ALTER TABLE "members" ADD CONSTRAINT "members_transferred_from_member_id_fkey" FOREIGN KEY ("transferred_from_member_id") REFERENCES "members"("id");

-- households.head_member_id -> members is the one genuinely circular FK
-- (members.household_id -> households, households.head_member_id ->
-- members); added last, after both tables exist, same as the original
-- SQLAlchemy design's `use_alter=True`.
ALTER TABLE "households" ADD CONSTRAINT "households_head_member_id_fkey" FOREIGN KEY ("head_member_id") REFERENCES "members"("id");

ALTER TABLE "sacramental_records" ADD CONSTRAINT "sacramental_records_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "sacramental_records" ADD CONSTRAINT "sacramental_records_member_id_fkey" FOREIGN KEY ("member_id") REFERENCES "members"("id") ON DELETE CASCADE;

ALTER TABLE "pastoral_notes" ADD CONSTRAINT "pastoral_notes_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "pastoral_notes" ADD CONSTRAINT "pastoral_notes_member_id_fkey" FOREIGN KEY ("member_id") REFERENCES "members"("id") ON DELETE CASCADE;
ALTER TABLE "pastoral_notes" ADD CONSTRAINT "pastoral_notes_author_user_id_fkey" FOREIGN KEY ("author_user_id") REFERENCES "users"("id");

ALTER TABLE "member_transfers" ADD CONSTRAINT "member_transfers_from_tenant_id_fkey" FOREIGN KEY ("from_tenant_id") REFERENCES "organizations"("id");
ALTER TABLE "member_transfers" ADD CONSTRAINT "member_transfers_to_tenant_id_fkey" FOREIGN KEY ("to_tenant_id") REFERENCES "organizations"("id");
ALTER TABLE "member_transfers" ADD CONSTRAINT "member_transfers_from_member_id_fkey" FOREIGN KEY ("from_member_id") REFERENCES "members"("id");
ALTER TABLE "member_transfers" ADD CONSTRAINT "member_transfers_to_member_id_fkey" FOREIGN KEY ("to_member_id") REFERENCES "members"("id");
ALTER TABLE "member_transfers" ADD CONSTRAINT "member_transfers_requested_by_user_id_fkey" FOREIGN KEY ("requested_by_user_id") REFERENCES "users"("id");
ALTER TABLE "member_transfers" ADD CONSTRAINT "member_transfers_approved_by_user_id_fkey" FOREIGN KEY ("approved_by_user_id") REFERENCES "users"("id");
ALTER TABLE "member_transfers" ADD CONSTRAINT "member_transfers_check" CHECK ("from_tenant_id" <> "to_tenant_id");
ALTER TABLE "member_transfers" ADD CONSTRAINT "member_transfers_status_check" CHECK ("status" IN ('pending','approved','completed','rejected','canceled'));
ALTER TABLE "member_transfers" ADD CONSTRAINT "member_transfers_history_scope_check" CHECK ("history_scope" IN ('identity_only','include_sacraments','full_history'));

ALTER TABLE "events" ADD CONSTRAINT "events_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "events" ADD CONSTRAINT "events_unit_id_fkey" FOREIGN KEY ("unit_id") REFERENCES "hierarchy_units"("id") ON DELETE SET NULL;
ALTER TABLE "events" ADD CONSTRAINT "events_created_by_user_id_fkey" FOREIGN KEY ("created_by_user_id") REFERENCES "users"("id");
ALTER TABLE "events" ADD CONSTRAINT "events_type_check" CHECK ("type" IN ('service','meeting','event','prayer','outreach','other'));
ALTER TABLE "events" ADD CONSTRAINT "events_status_check" CHECK ("status" IN ('draft','published','canceled','completed'));
ALTER TABLE "events" ADD CONSTRAINT "events_attendance_mode_check" CHECK ("attendance_mode" IN ('individual','headcount'));

ALTER TABLE "attendance_records" ADD CONSTRAINT "attendance_records_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "attendance_records" ADD CONSTRAINT "attendance_records_event_id_fkey" FOREIGN KEY ("event_id") REFERENCES "events"("id") ON DELETE CASCADE;
ALTER TABLE "attendance_records" ADD CONSTRAINT "attendance_records_member_id_fkey" FOREIGN KEY ("member_id") REFERENCES "members"("id") ON DELETE CASCADE;
ALTER TABLE "attendance_records" ADD CONSTRAINT "attendance_records_marked_by_user_id_fkey" FOREIGN KEY ("marked_by_user_id") REFERENCES "users"("id");
ALTER TABLE "attendance_records" ADD CONSTRAINT "attendance_records_mode_check" CHECK ("mode" IN ('individual','headcount'));

ALTER TABLE "funds" ADD CONSTRAINT "funds_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;

ALTER TABLE "donation_batches" ADD CONSTRAINT "donation_batches_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "donation_batches" ADD CONSTRAINT "donation_batches_service_id_fkey" FOREIGN KEY ("service_id") REFERENCES "events"("id");
ALTER TABLE "donation_batches" ADD CONSTRAINT "donation_batches_created_by_user_id_fkey" FOREIGN KEY ("created_by_user_id") REFERENCES "users"("id");
ALTER TABLE "donation_batches" ADD CONSTRAINT "donation_batches_status_check" CHECK ("status" IN ('open','closed','posted'));

ALTER TABLE "donations" ADD CONSTRAINT "donations_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "donations" ADD CONSTRAINT "donations_batch_id_fkey" FOREIGN KEY ("batch_id") REFERENCES "donation_batches"("id") ON DELETE CASCADE;
ALTER TABLE "donations" ADD CONSTRAINT "donations_member_id_fkey" FOREIGN KEY ("member_id") REFERENCES "members"("id");
ALTER TABLE "donations" ADD CONSTRAINT "donations_fund_id_fkey" FOREIGN KEY ("fund_id") REFERENCES "funds"("id");
ALTER TABLE "donations" ADD CONSTRAINT "donations_voided_by_user_id_fkey" FOREIGN KEY ("voided_by_user_id") REFERENCES "users"("id");
ALTER TABLE "donations" ADD CONSTRAINT "donations_created_by_user_id_fkey" FOREIGN KEY ("created_by_user_id") REFERENCES "users"("id");
ALTER TABLE "donations" ADD CONSTRAINT "donations_amount_positive_check" CHECK ("amount" > 0);
ALTER TABLE "donations" ADD CONSTRAINT "donations_payment_method_check" CHECK ("payment_method" IN ('cash','check','card','transfer','mobile_money'));

ALTER TABLE "pledges" ADD CONSTRAINT "pledges_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "pledges" ADD CONSTRAINT "pledges_member_id_fkey" FOREIGN KEY ("member_id") REFERENCES "members"("id");
ALTER TABLE "pledges" ADD CONSTRAINT "pledges_fund_id_fkey" FOREIGN KEY ("fund_id") REFERENCES "funds"("id");
ALTER TABLE "pledges" ADD CONSTRAINT "pledges_frequency_check" CHECK ("frequency" IN ('weekly','monthly','annual','one-time'));
ALTER TABLE "pledges" ADD CONSTRAINT "pledges_status_check" CHECK ("status" IN ('active','fulfilled','overdue','canceled'));

ALTER TABLE "announcements" ADD CONSTRAINT "announcements_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "announcements" ADD CONSTRAINT "announcements_author_user_id_fkey" FOREIGN KEY ("author_user_id") REFERENCES "users"("id");
ALTER TABLE "announcements" ADD CONSTRAINT "announcements_status_check" CHECK ("status" IN ('draft','scheduled','sent','archived'));

ALTER TABLE "message_templates" ADD CONSTRAINT "message_templates_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;

ALTER TABLE "notifications" ADD CONSTRAINT "notifications_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "notifications" ADD CONSTRAINT "notifications_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id");
ALTER TABLE "notifications" ADD CONSTRAINT "notifications_type_check" CHECK ("type" IN ('pastoral_alert','finance_alert','system','announcement'));

ALTER TABLE "certificate_templates" ADD CONSTRAINT "certificate_templates_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "certificate_templates" ADD CONSTRAINT "certificate_templates_category_check" CHECK ("category" IN ('sacramental','membership','recognition','education'));
ALTER TABLE "certificate_templates" ADD CONSTRAINT "certificate_templates_status_check" CHECK ("status" IN ('active','draft','archived'));

ALTER TABLE "certificates" ADD CONSTRAINT "certificates_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "certificates" ADD CONSTRAINT "certificates_template_id_fkey" FOREIGN KEY ("template_id") REFERENCES "certificate_templates"("id");
ALTER TABLE "certificates" ADD CONSTRAINT "certificates_member_id_fkey" FOREIGN KEY ("member_id") REFERENCES "members"("id");
ALTER TABLE "certificates" ADD CONSTRAINT "certificates_issued_by_user_id_fkey" FOREIGN KEY ("issued_by_user_id") REFERENCES "users"("id");

ALTER TABLE "media_assets" ADD CONSTRAINT "media_assets_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "media_assets" ADD CONSTRAINT "media_assets_uploaded_by_user_id_fkey" FOREIGN KEY ("uploaded_by_user_id") REFERENCES "users"("id");
ALTER TABLE "media_assets" ADD CONSTRAINT "media_assets_kind_check" CHECK ("kind" IN ('image','video','document'));

ALTER TABLE "media_attachments" ADD CONSTRAINT "media_attachments_media_id_fkey" FOREIGN KEY ("media_id") REFERENCES "media_assets"("id") ON DELETE CASCADE;

ALTER TABLE "audit_logs" ADD CONSTRAINT "audit_logs_tenant_id_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id");
ALTER TABLE "audit_logs" ADD CONSTRAINT "audit_logs_actor_user_id_fkey" FOREIGN KEY ("actor_user_id") REFERENCES "users"("id");
ALTER TABLE "audit_logs" ADD CONSTRAINT "audit_logs_impersonated_by_user_id_fkey" FOREIGN KEY ("impersonated_by_user_id") REFERENCES "users"("id");

ALTER TABLE "organizations" ADD CONSTRAINT "organizations_status_check" CHECK ("status" IN ('trial','active','suspended','canceled'));
ALTER TABLE "organizations" ADD CONSTRAINT "organizations_tier_check" CHECK ("tier" IN ('free','seed','parish','growth','diocese','enterprise'));

ALTER TABLE "members" ADD CONSTRAINT "members_status_check" CHECK ("status" IN ('active','visitor','inactive','prospect'));

-- ── Extra indexes (tenant_id leads every tenant-scoped table's index, so
--    the RLS policy's filter is always the planner's first option) ────────
CREATE INDEX "organizations_status_idx" ON "organizations" ("status");
CREATE INDEX "organizations_public_lookup_idx" ON "organizations" ("slug") WHERE "status" IN ('trial','active');
CREATE INDEX "tenant_memberships_tenant_idx" ON "tenant_memberships" ("tenant_id");
CREATE INDEX "tenant_memberships_user_idx" ON "tenant_memberships" ("user_id");
CREATE UNIQUE INDEX "tenant_memberships_one_primary_idx" ON "tenant_memberships" ("user_id") WHERE "is_primary";
CREATE INDEX "hierarchy_units_tenant_idx" ON "hierarchy_units" ("tenant_id");
CREATE INDEX "hierarchy_closure_descendant_idx" ON "hierarchy_closure" ("descendant_id");
CREATE INDEX "households_tenant_idx" ON "households" ("tenant_id");
CREATE INDEX "members_tenant_idx" ON "members" ("tenant_id");
CREATE INDEX "members_tenant_envelope_idx" ON "members" ("tenant_id", "envelope_number");
CREATE INDEX "members_household_idx" ON "members" ("household_id");
CREATE UNIQUE INDEX "members_tenant_user_idx" ON "members" ("tenant_id", "user_id") WHERE "user_id" IS NOT NULL;
CREATE INDEX "sacramental_records_member_idx" ON "sacramental_records" ("tenant_id", "member_id");
CREATE INDEX "pastoral_notes_member_idx" ON "pastoral_notes" ("tenant_id", "member_id");
CREATE INDEX "member_transfers_from_idx" ON "member_transfers" ("from_tenant_id");
CREATE INDEX "member_transfers_to_idx" ON "member_transfers" ("to_tenant_id");
CREATE INDEX "member_transfers_member_idx" ON "member_transfers" ("from_member_id");
CREATE INDEX "events_tenant_start_idx" ON "events" ("tenant_id", "start_date_time");
CREATE INDEX "attendance_tenant_event_idx" ON "attendance_records" ("tenant_id", "event_id");
CREATE INDEX "funds_tenant_idx" ON "funds" ("tenant_id");
CREATE INDEX "donation_batches_tenant_status_idx" ON "donation_batches" ("tenant_id", "status");
CREATE INDEX "donations_tenant_batch_idx" ON "donations" ("tenant_id", "batch_id");
CREATE INDEX "donations_tenant_member_idx" ON "donations" ("tenant_id", "member_id");
CREATE INDEX "pledges_tenant_member_idx" ON "pledges" ("tenant_id", "member_id");
CREATE INDEX "announcements_tenant_status_idx" ON "announcements" ("tenant_id", "status");
CREATE INDEX "notifications_tenant_user_unread_idx" ON "notifications" ("tenant_id", "user_id", "is_read");
CREATE INDEX "certificates_tenant_member_idx" ON "certificates" ("tenant_id", "member_id");
CREATE INDEX "media_assets_tenant_idx" ON "media_assets" ("tenant_id");
CREATE INDEX "media_attachments_target_idx" ON "media_attachments" ("attachable_type", "attachable_id");
CREATE INDEX "audit_logs_tenant_created_idx" ON "audit_logs" ("tenant_id", "created_at" DESC);
CREATE INDEX "audit_logs_actor_idx" ON "audit_logs" ("actor_user_id");

-- ── RLS helper functions ─────────────────────────────────────────────────
-- set_config(name, value, true) ("SET LOCAL" semantics) does not revert an
-- unset custom GUC to NULL after commit — the first time a custom GUC is
-- touched on a pooled connection, its post-commit baseline is an EMPTY
-- STRING, not NULL, and ''::uuid is a hard cast error, not a harmless
-- non-match. nullif(..., '') guards every policy below against that.
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.tenant_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION current_app_user_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('app.user_id', true), '')::uuid
$$;

-- ── Row-Level Security ───────────────────────────────────────────────────
DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN
    SELECT DISTINCT c.relname AS table_name
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE a.attname = 'tenant_id'
      AND a.attnotnull
      AND c.relkind = 'r'
      AND n.nspname = 'public'
      AND c.relname NOT IN ('member_transfers', 'tenant_memberships')
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', r.table_name);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_tenant_id())',
      r.table_name
    );
  END LOOP;
END $$;

ALTER TABLE "tenant_memberships" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "tenant_memberships"
USING (tenant_id = current_tenant_id() OR user_id = current_app_user_id())
WITH CHECK (tenant_id = current_tenant_id());

ALTER TABLE "member_transfers" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "member_transfers"
USING (current_tenant_id() IN (from_tenant_id, to_tenant_id));

-- roles: nullable tenant_id (system roles) means the generic ENABLE loop's
-- attnotnull condition skips it entirely — closed explicitly here so one
-- tenant's custom roles aren't readable by every other tenant's connection.
ALTER TABLE "roles" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "roles"
USING (tenant_id IS NULL OR tenant_id = current_tenant_id())
WITH CHECK (tenant_id IS NULL OR tenant_id = current_tenant_id());

ALTER TABLE "role_permissions" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "role_permissions"
USING (
  EXISTS (
    SELECT 1 FROM "roles" r
    WHERE r.id = role_permissions.role_id
      AND (r.tenant_id IS NULL OR r.tenant_id = current_tenant_id())
  )
);

ALTER TABLE "audit_logs" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "audit_logs"
USING (tenant_id = current_tenant_id());

ALTER TABLE "hierarchy_closure" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "hierarchy_closure"
USING (
  EXISTS (
    SELECT 1 FROM "hierarchy_units" hu
    WHERE hu.id = hierarchy_closure.ancestor_id
      AND hu.tenant_id = current_tenant_id()
  )
);

ALTER TABLE "media_attachments" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "media_attachments"
USING (
  EXISTS (
    SELECT 1 FROM "media_assets" ma
    WHERE ma.id = media_attachments.media_id
      AND ma.tenant_id = current_tenant_id()
  )
);

-- ── Closure-table maintenance trigger ────────────────────────────────────
CREATE OR REPLACE FUNCTION maintain_hierarchy_closure() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO hierarchy_closure (ancestor_id, descendant_id, depth)
  VALUES (NEW.id, NEW.id, 0);

  IF NEW.parent_id IS NOT NULL THEN
    INSERT INTO hierarchy_closure (ancestor_id, descendant_id, depth)
    SELECT ancestor_id, NEW.id, depth + 1
    FROM hierarchy_closure
    WHERE descendant_id = NEW.parent_id;
  END IF;

  RETURN NEW;
END;
$$;

CREATE TRIGGER hierarchy_units_closure_insert
AFTER INSERT ON "hierarchy_units"
FOR EACH ROW EXECUTE FUNCTION maintain_hierarchy_closure();

-- ── Member transfer — the one cross-tenant write path ────────────────────
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

  UPDATE members
     SET status = 'inactive', transferred_to_member_id = v_new_id, updated_at = now()
   WHERE id = v_transfer.from_member_id;

  UPDATE member_transfers
     SET status = 'completed', to_member_id = v_new_id, completed_at = now()
   WHERE id = p_transfer_id;

  RETURN v_new_id;
END;
$$;

-- ── System roles + permissions, copied from src/hooks/useRole.ts's
--    ROLE_PERMISSIONS (member/staff/board), so enforcement never silently
--    diverges from what the frontend already assumes a role can do. ───────
INSERT INTO "roles" (id, tenant_id, name, is_system, created_at) VALUES
  ('00000000-0000-0000-0000-000000000001', NULL, 'member', true, now()),
  ('00000000-0000-0000-0000-000000000002', NULL, 'staff',  true, now()),
  ('00000000-0000-0000-0000-000000000003', NULL, 'board',  true, now());

INSERT INTO "role_permissions" (role_id, permission) VALUES
  ('00000000-0000-0000-0000-000000000001', 'portal:view'),
  ('00000000-0000-0000-0000-000000000001', 'profile:read'),
  ('00000000-0000-0000-0000-000000000001', 'profile:update_own'),
  ('00000000-0000-0000-0000-000000000001', 'events:read'),
  ('00000000-0000-0000-0000-000000000001', 'announcements:read'),
  ('00000000-0000-0000-0000-000000000001', 'giving:read_own'),
  ('00000000-0000-0000-0000-000000000001', 'notifications:read'),
  ('00000000-0000-0000-0000-000000000002', 'portal:view'),
  ('00000000-0000-0000-0000-000000000002', 'members:read'),
  ('00000000-0000-0000-0000-000000000002', 'members:create'),
  ('00000000-0000-0000-0000-000000000002', 'members:update'),
  ('00000000-0000-0000-0000-000000000002', 'attendance:read'),
  ('00000000-0000-0000-0000-000000000002', 'attendance:create'),
  ('00000000-0000-0000-0000-000000000002', 'events:read'),
  ('00000000-0000-0000-0000-000000000002', 'events:create'),
  ('00000000-0000-0000-0000-000000000002', 'events:update'),
  ('00000000-0000-0000-0000-000000000002', 'finance:read'),
  ('00000000-0000-0000-0000-000000000002', 'finance:create'),
  ('00000000-0000-0000-0000-000000000002', 'finance:update'),
  ('00000000-0000-0000-0000-000000000002', 'announcements:read'),
  ('00000000-0000-0000-0000-000000000002', 'announcements:create'),
  ('00000000-0000-0000-0000-000000000002', 'certificates:read'),
  ('00000000-0000-0000-0000-000000000002', 'certificates:create'),
  ('00000000-0000-0000-0000-000000000002', 'dashboard:view'),
  ('00000000-0000-0000-0000-000000000002', 'profile:read'),
  ('00000000-0000-0000-0000-000000000002', 'profile:update_own'),
  ('00000000-0000-0000-0000-000000000003', 'members:read'),
  ('00000000-0000-0000-0000-000000000003', 'members:create'),
  ('00000000-0000-0000-0000-000000000003', 'members:update'),
  ('00000000-0000-0000-0000-000000000003', 'members:export'),
  ('00000000-0000-0000-0000-000000000003', 'attendance:read'),
  ('00000000-0000-0000-0000-000000000003', 'attendance:create'),
  ('00000000-0000-0000-0000-000000000003', 'events:read'),
  ('00000000-0000-0000-0000-000000000003', 'events:create'),
  ('00000000-0000-0000-0000-000000000003', 'events:update'),
  ('00000000-0000-0000-0000-000000000003', 'finance:read'),
  ('00000000-0000-0000-0000-000000000003', 'finance:create'),
  ('00000000-0000-0000-0000-000000000003', 'finance:update'),
  ('00000000-0000-0000-0000-000000000003', 'finance:export'),
  ('00000000-0000-0000-0000-000000000003', 'announcements:read'),
  ('00000000-0000-0000-0000-000000000003', 'announcements:create'),
  ('00000000-0000-0000-0000-000000000003', 'certificates:read'),
  ('00000000-0000-0000-0000-000000000003', 'certificates:create'),
  ('00000000-0000-0000-0000-000000000003', 'dashboard:view'),
  ('00000000-0000-0000-0000-000000000003', 'hierarchy:read'),
  ('00000000-0000-0000-0000-000000000003', 'hierarchy:update'),
  ('00000000-0000-0000-0000-000000000003', 'team:read'),
  ('00000000-0000-0000-0000-000000000003', 'team:invite'),
  ('00000000-0000-0000-0000-000000000003', 'roles:read'),
  ('00000000-0000-0000-0000-000000000003', 'roles:create'),
  ('00000000-0000-0000-0000-000000000003', 'analytics:read'),
  ('00000000-0000-0000-0000-000000000003', 'org:read'),
  ('00000000-0000-0000-0000-000000000003', 'org:settings'),
  ('00000000-0000-0000-0000-000000000003', 'profile:read'),
  ('00000000-0000-0000-0000-000000000003', 'profile:update_own'),
  ('00000000-0000-0000-0000-000000000003', 'pastoral_notes:read'),
  ('00000000-0000-0000-0000-000000000003', 'pastoral_notes:write'),
  ('00000000-0000-0000-0000-000000000003', 'network:read'),
  ('00000000-0000-0000-0000-000000000003', 'parish_comparison:read'),
  ('00000000-0000-0000-0000-000000000003', 'domain:manage'),
  ('00000000-0000-0000-0000-000000000003', 'white_label:manage');

-- ── Two database roles matching the two frontend access paths. Passwords
--    are placeholders here (DB roles aren't re-created if they already
--    exist) — set the real ones via ALTER ROLE ... PASSWORD or recreate the
--    database if you need to change them; they must match
--    APP_TENANT_DB_PASSWORD / APP_PLATFORM_DB_PASSWORD in .env. ────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_tenant') THEN
    CREATE ROLE app_tenant LOGIN PASSWORD 'change_me_tenant_pw';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_platform') THEN
    CREATE ROLE app_platform LOGIN PASSWORD 'change_me_platform_pw' BYPASSRLS;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO app_tenant, app_platform;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_tenant, app_platform;
GRANT EXECUTE ON FUNCTION execute_member_transfer(uuid) TO app_tenant, app_platform;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_tenant, app_platform;
