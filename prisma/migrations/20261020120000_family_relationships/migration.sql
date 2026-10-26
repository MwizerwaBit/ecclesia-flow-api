-- Family relationships between people: parent, legal guardian, spouse.
--
-- A row reads "related_member is the <kind> of member". A person who is a
-- parent of two children has two rows (one per child). A spouse pair is ONE
-- row, stored with the smaller id first, so the same marriage can't be
-- recorded twice from either side.
--
-- These relationships carry their own authorization (see
-- app/modules/family): a parent or guardian may act for a person while that
-- person is under 18, and for no one afterwards. A spouse relationship grants
-- no access to anything. Rows are never deleted when a relationship ends;
-- `ended_on` closes them so there is a history.
CREATE TABLE "member_relationships" (
    "id"                 UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"          UUID NOT NULL,
    "member_id"          UUID NOT NULL,
    "related_member_id"  UUID NOT NULL,
    "kind"               TEXT NOT NULL,
    "started_on"         DATE NOT NULL DEFAULT CURRENT_DATE,
    "ended_on"           DATE,
    "created_by_user_id" UUID,
    "ended_by_user_id"   UUID,
    "created_at"         TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "member_relationships_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "member_relationships_kind_check" CHECK ("kind" IN ('parent', 'guardian', 'spouse')),
    CONSTRAINT "member_relationships_not_self_check" CHECK ("member_id" <> "related_member_id"),
    CONSTRAINT "member_relationships_spouse_order_check" CHECK ("kind" <> 'spouse' OR "member_id" < "related_member_id"),
    CONSTRAINT "member_relationships_dates_check" CHECK ("ended_on" IS NULL OR "ended_on" >= "started_on")
);
ALTER TABLE "member_relationships" ADD CONSTRAINT "member_relationships_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "member_relationships" ADD CONSTRAINT "member_relationships_member_fkey" FOREIGN KEY ("member_id") REFERENCES "members"("id") ON DELETE CASCADE;
ALTER TABLE "member_relationships" ADD CONSTRAINT "member_relationships_related_fkey" FOREIGN KEY ("related_member_id") REFERENCES "members"("id") ON DELETE CASCADE;
ALTER TABLE "member_relationships" ADD CONSTRAINT "member_relationships_created_by_fkey" FOREIGN KEY ("created_by_user_id") REFERENCES "users"("id") ON DELETE SET NULL;
ALTER TABLE "member_relationships" ADD CONSTRAINT "member_relationships_ended_by_fkey" FOREIGN KEY ("ended_by_user_id") REFERENCES "users"("id") ON DELETE SET NULL;

-- One live row per pair and kind.
CREATE UNIQUE INDEX "member_relationships_live_uniq" ON "member_relationships" ("member_id", "related_member_id", "kind") WHERE "ended_on" IS NULL;
CREATE INDEX "member_relationships_member_idx" ON "member_relationships" ("member_id") WHERE "ended_on" IS NULL;
CREATE INDEX "member_relationships_related_idx" ON "member_relationships" ("related_member_id") WHERE "ended_on" IS NULL;

-- ═══════════════════════════ Row-level security ═══════════════════════════
-- Tenant isolation, plus: you only see (or create) a relationship whose
-- SUBJECT person you can already see. The subquery on "members" is itself
-- subject to the members unit-scope policies, so a branch-scoped session
-- inherits exactly its own people and nobody else's, without this table
-- needing a unit column.
ALTER TABLE "member_relationships" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "member_relationships"
    USING (
        tenant_id = current_tenant_id()
        AND EXISTS (SELECT 1 FROM "members" m WHERE m.id = "member_relationships"."member_id")
    );

GRANT SELECT, INSERT, UPDATE, DELETE ON "member_relationships" TO app_tenant, app_platform;
