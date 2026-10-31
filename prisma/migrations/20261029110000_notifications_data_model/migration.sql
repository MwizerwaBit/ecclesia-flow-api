-- Notifications data model (TODO.md, Notifications module N1).
--
-- Expand step only: every change is additive or widens a check, so the release
-- that is already running keeps working against it (it writes is_read and the
-- original four types and never touches the new columns).
--
-- notifications
--   dedupe_key     a stable key per trigger ("event.published:<event id>"); the
--                  partial unique index below turns a repeated trigger into a
--                  no-op instead of a second row. NULL = never deduped.
--   resource_type / resource_id
--                  what the notification is about (an event, a payment ...), so
--                  the link target can be re-checked when it is opened. Both or
--                  neither.
--   actor_user_id  who caused it (the admin who sent a manual notification, the
--                  reviewer who approved an event). NULL for system triggers.
--   read_at        when it was read. is_read stays the source of truth for the
--                  release that is running; new code writes both. Rows read
--                  before this migration have no read_at (the time was never
--                  recorded) and are not backfilled with a guess.
--   type           widened for the new kinds: event, approval, billing,
--                  reminder, manual.
--
-- notification_deliveries
--   One row per (notification, channel) that goes through an outside provider.
--   In-app delivery is the notifications row itself and needs no row here, so
--   a provider outage can never hide a notification. status:
--     pending  waiting for its first attempt, or for a retry (next_attempt_at)
--     sent     the provider accepted it
--     failed   gave up after the maximum number of attempts
--     skipped  not sent on purpose (the user turned the channel off, or has no
--              address)
--   last_error is a short, scrubbed reason; the recipient's address is never
--   stored here (it is read from the user at send time).
--
-- Locking: both tables are small and the indexes are built without
-- CONCURRENTLY inside the migration transaction; the lock is brief.

-- ───────────────────────────── notifications ─────────────────────────────

ALTER TABLE "notifications"
    ADD COLUMN "read_at"       TIMESTAMPTZ(6),
    ADD COLUMN "dedupe_key"    TEXT,
    ADD COLUMN "resource_type" TEXT,
    ADD COLUMN "resource_id"   UUID,
    ADD COLUMN "actor_user_id" UUID;

ALTER TABLE "notifications" ADD CONSTRAINT "notifications_actor_user_id_fkey"
    FOREIGN KEY ("actor_user_id") REFERENCES "users"("id") ON DELETE SET NULL;

ALTER TABLE "notifications" DROP CONSTRAINT "notifications_type_check";
ALTER TABLE "notifications" ADD CONSTRAINT "notifications_type_check" CHECK (
    "type" IN ('pastoral_alert', 'finance_alert', 'system', 'announcement',
               'event', 'approval', 'billing', 'reminder', 'manual')
);

ALTER TABLE "notifications" ADD CONSTRAINT "notifications_dedupe_key_check"
    CHECK ("dedupe_key" IS NULL OR char_length("dedupe_key") BETWEEN 1 AND 200);
ALTER TABLE "notifications" ADD CONSTRAINT "notifications_resource_check" CHECK (
    ("resource_type" IS NULL AND "resource_id" IS NULL)
    OR ("resource_type" IS NOT NULL AND "resource_id" IS NOT NULL AND char_length("resource_type") BETWEEN 1 AND 50)
);
-- A read time only on a read row: marking it unread again must clear read_at.
ALTER TABLE "notifications" ADD CONSTRAINT "notifications_read_at_check"
    CHECK ("read_at" IS NULL OR "is_read");

-- One notification per user per trigger.
CREATE UNIQUE INDEX "notifications_dedupe_key" ON "notifications" ("tenant_id", "user_id", "dedupe_key")
    WHERE "dedupe_key" IS NOT NULL;
-- The list: a user's newest first, with id as the tie-breaker for a stable page.
CREATE INDEX "notifications_user_recent_idx" ON "notifications" ("tenant_id", "user_id", "created_at" DESC, "id" DESC);
-- The unread count behind the bell, polled often.
CREATE INDEX "notifications_user_unread_idx" ON "notifications" ("tenant_id", "user_id") WHERE NOT "is_read";
-- Target of the composite foreign key below, so a delivery row can only ever
-- point at a notification of the same church and the same recipient.
CREATE UNIQUE INDEX "notifications_id_tenant_user_key" ON "notifications" ("id", "tenant_id", "user_id");

-- The older (tenant_id, user_id, is_read) index is now covered by the two
-- above but is left in place: dropping it is a contract step for a later release.

-- ───────────────────────────── notification_deliveries ─────────────────────────────

CREATE TABLE "notification_deliveries" (
    "id"              UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"       UUID NOT NULL,
    "notification_id" UUID NOT NULL,
    "user_id"         UUID NOT NULL,
    "channel"         TEXT NOT NULL,
    "status"          TEXT NOT NULL DEFAULT 'pending',
    "attempts"        INTEGER NOT NULL DEFAULT 0,
    "last_error"      TEXT,
    "next_attempt_at" TIMESTAMPTZ(6),
    "sent_at"         TIMESTAMPTZ(6),
    "created_at"      TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at"      TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "notification_deliveries_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "notification_deliveries_channel_check" CHECK ("channel" IN ('email', 'push', 'sms')),
    CONSTRAINT "notification_deliveries_status_check" CHECK ("status" IN ('pending', 'sent', 'failed', 'skipped')),
    CONSTRAINT "notification_deliveries_attempts_check" CHECK ("attempts" >= 0),
    CONSTRAINT "notification_deliveries_error_check" CHECK ("last_error" IS NULL OR char_length("last_error") <= 500),
    -- A sent row says when it was sent.
    CONSTRAINT "notification_deliveries_sent_check" CHECK ("status" <> 'sent' OR "sent_at" IS NOT NULL)
);

ALTER TABLE "notification_deliveries" ADD CONSTRAINT "notification_deliveries_tenant_id_fkey"
    FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
-- Same church and same recipient as the notification it delivers.
ALTER TABLE "notification_deliveries" ADD CONSTRAINT "notification_deliveries_notification_fkey"
    FOREIGN KEY ("notification_id", "tenant_id", "user_id")
    REFERENCES "notifications" ("id", "tenant_id", "user_id") ON DELETE CASCADE;

-- One delivery per notification per channel: a retry updates the row.
CREATE UNIQUE INDEX "notification_deliveries_channel_key" ON "notification_deliveries" ("notification_id", "channel");
-- What the retry job scans: rows still waiting, soonest first.
CREATE INDEX "notification_deliveries_due_idx" ON "notification_deliveries" ("next_attempt_at") WHERE "status" = 'pending';
CREATE INDEX "notification_deliveries_tenant_idx" ON "notification_deliveries" ("tenant_id", "created_at" DESC);

ALTER TABLE "notification_deliveries" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON "notification_deliveries" USING (tenant_id = current_tenant_id());
GRANT SELECT, INSERT, UPDATE ON "notification_deliveries" TO app_tenant;
GRANT SELECT, INSERT, UPDATE, DELETE ON "notification_deliveries" TO app_platform;
-- No DELETE for the church's own connection: delivery history goes only with its
-- notification. The REVOKE is needed because the default privileges set in the
-- init migration already hand app_tenant DELETE on every table created later.
REVOKE DELETE ON "notification_deliveries" FROM app_tenant;
