-- Manual (mobile-money) payments: a church sends money to the platform's MoMo
-- number, uploads proof, and a platform admin approves it, which grants the
-- months paid for. No card processor involved; the Stripe path stays as is.
--
-- A church can create its own row (status 'pending') and read it back, and that
-- is all: the tenant role has no UPDATE or DELETE here, so approval can only
-- ever happen on the platform connection, whatever the application code does.

CREATE TABLE "billing_manual_payments" (
    "id"                    UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"             UUID NOT NULL,
    "tier"                  TEXT NOT NULL,
    "months"                INTEGER NOT NULL,
    -- What the plan costs for those months, worked out by the server.
    "amount_cents"          INTEGER NOT NULL,
    "currency"              TEXT NOT NULL,
    -- Printed on the payment screen; the church puts it in the MoMo message.
    "reference_code"        TEXT NOT NULL,
    -- What the church says it sent.
    "sender_name"           TEXT NOT NULL,
    "sent_amount_cents"     INTEGER NOT NULL,
    "momo_transaction_id"   TEXT,
    "proof_storage_key"     TEXT NOT NULL,
    "proof_mime_type"       TEXT NOT NULL,
    "proof_size_bytes"      INTEGER NOT NULL,
    "proof_sha256"          TEXT NOT NULL,
    "status"                TEXT NOT NULL DEFAULT 'pending',
    "submitted_by_user_id"  UUID NOT NULL,
    "submitted_at"          TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "decided_by_user_id"    UUID,
    "decided_at"            TIMESTAMPTZ(6),
    "decision_note"         TEXT,
    "granted_from"          TIMESTAMPTZ(6),
    "granted_until"         TIMESTAMPTZ(6),
    CONSTRAINT "billing_manual_payments_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "billing_manual_payments_months_check" CHECK ("months" BETWEEN 1 AND 36),
    CONSTRAINT "billing_manual_payments_amount_check" CHECK ("amount_cents" > 0 AND "sent_amount_cents" > 0),
    CONSTRAINT "billing_manual_payments_status_check" CHECK ("status" IN ('pending', 'approved', 'rejected')),
    -- An approved row always says who approved it and what it granted.
    CONSTRAINT "billing_manual_payments_decision_check" CHECK (
        "status" = 'pending'
        OR ("decided_by_user_id" IS NOT NULL AND "decided_at" IS NOT NULL)
    ),
    CONSTRAINT "billing_manual_payments_grant_check" CHECK (
        "status" <> 'approved' OR ("granted_from" IS NOT NULL AND "granted_until" IS NOT NULL)
    )
);
ALTER TABLE "billing_manual_payments" ADD CONSTRAINT "billing_manual_payments_tenant_fkey"
    FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;

CREATE UNIQUE INDEX "billing_manual_payments_reference_key" ON "billing_manual_payments" ("reference_code");
-- One request waiting at a time per church: no flooding the approval queue.
CREATE UNIQUE INDEX "billing_manual_payments_one_pending" ON "billing_manual_payments" ("tenant_id") WHERE "status" = 'pending';
-- The same screenshot, or the same MoMo transaction, can't pay twice.
CREATE UNIQUE INDEX "billing_manual_payments_proof_once" ON "billing_manual_payments" ("proof_sha256") WHERE "status" IN ('pending', 'approved');
CREATE UNIQUE INDEX "billing_manual_payments_txn_once" ON "billing_manual_payments" (lower("momo_transaction_id"))
    WHERE "momo_transaction_id" IS NOT NULL AND "status" IN ('pending', 'approved');
CREATE INDEX "billing_manual_payments_status_idx" ON "billing_manual_payments" ("status", "submitted_at");

ALTER TABLE "billing_manual_payments" ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_read ON "billing_manual_payments" FOR SELECT TO app_tenant USING (tenant_id = current_tenant_id());
CREATE POLICY tenant_submit ON "billing_manual_payments" FOR INSERT TO app_tenant
    WITH CHECK (tenant_id = current_tenant_id() AND status = 'pending' AND decided_by_user_id IS NULL);
GRANT SELECT, INSERT ON "billing_manual_payments" TO app_tenant;
GRANT SELECT, INSERT, UPDATE ON "billing_manual_payments" TO app_platform;

-- When the "your plan ends soon" email last went out, and for which end date,
-- so it is sent once per period however often the job runs.
ALTER TABLE "billing_subscriptions" ADD COLUMN "renewal_reminder_for" TIMESTAMPTZ(6);

-- A church's own session only ever reads its subscription and payments; they are
-- written by the platform connection (webhooks, approvals). Take the write
-- grants away so a bug or an injection in a tenant request can't extend a plan.
REVOKE INSERT, UPDATE, DELETE ON "billing_subscriptions", "billing_payments" FROM app_tenant;
