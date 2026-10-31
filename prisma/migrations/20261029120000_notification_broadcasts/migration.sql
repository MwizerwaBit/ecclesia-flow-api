-- Manual notifications from an admin (TODO.md, Notifications module N6).
--
-- notifications:send is seeded for staff, board (administrator) and leader. The
-- code's SYSTEM_ROLE_PERMISSIONS is what the API checks; these rows keep the
-- database's own copy (role lists, custom-role views) in step. Idempotent.
--
-- notification_broadcasts is one row per send, for three jobs:
--   - idempotency: (tenant, sender, request_key) is unique, so a double click or
--     a retried request is answered with the first result instead of sending
--     again (the notifications themselves are also deduped on the same key);
--   - rate limiting: how many sends a person made in the last hour;
--   - a record of who sent to whom, in counts: it holds no message text (that is
--     in the notifications) and no names.

INSERT INTO "role_permissions" ("role_id", "permission") VALUES
  ('00000000-0000-0000-0000-000000000002', 'notifications:send'),
  ('00000000-0000-0000-0000-000000000003', 'notifications:send'),
  ('00000000-0000-0000-0000-000000000004', 'notifications:send')
ON CONFLICT DO NOTHING;

CREATE TABLE "notification_broadcasts" (
    "id"             UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"      UUID NOT NULL,
    "sender_user_id" UUID NOT NULL,
    "request_key"    TEXT NOT NULL,
    -- What was asked for: {"unit_ids": [...], "statuses": [...]}.
    "audience"       JSONB NOT NULL DEFAULT '{}',
    -- Members matched, and how they split.
    "audience_size"  INTEGER NOT NULL DEFAULT 0,
    "without_login"  INTEGER NOT NULL DEFAULT 0,
    "notified"       INTEGER NOT NULL DEFAULT 0,
    "opted_out"      INTEGER NOT NULL DEFAULT 0,
    "skipped"        INTEGER NOT NULL DEFAULT 0,
    "created_at"     TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "notification_broadcasts_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "notification_broadcasts_key_check" CHECK (char_length("request_key") BETWEEN 8 AND 100),
    CONSTRAINT "notification_broadcasts_counts_check" CHECK (
        "audience_size" >= 0 AND "without_login" >= 0 AND "notified" >= 0 AND "opted_out" >= 0 AND "skipped" >= 0
    )
);

ALTER TABLE "notification_broadcasts" ADD CONSTRAINT "notification_broadcasts_tenant_id_fkey"
    FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "notification_broadcasts" ADD CONSTRAINT "notification_broadcasts_sender_fkey"
    FOREIGN KEY ("sender_user_id") REFERENCES "users"("id");

-- One result per request key per sender.
CREATE UNIQUE INDEX "notification_broadcasts_request_key" ON "notification_broadcasts"
    ("tenant_id", "sender_user_id", "request_key");
-- "How many sends did this person make in the last hour".
CREATE INDEX "notification_broadcasts_sender_recent_idx" ON "notification_broadcasts"
    ("tenant_id", "sender_user_id", "created_at" DESC);

ALTER TABLE "notification_broadcasts" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "notification_broadcasts" USING (tenant_id = current_tenant_id());
GRANT SELECT, INSERT ON "notification_broadcasts" TO app_tenant;
GRANT SELECT, INSERT, UPDATE, DELETE ON "notification_broadcasts" TO app_platform;
-- A church's own connection can record a send but never rewrite or erase the record
-- (the default privileges from the init migration would otherwise grant both).
REVOKE UPDATE, DELETE ON "notification_broadcasts" FROM app_tenant;
