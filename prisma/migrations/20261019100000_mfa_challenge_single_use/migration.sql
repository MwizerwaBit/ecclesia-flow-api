-- MFA challenge tokens become single-use, capped-attempt (Phase 3 / SA-10).
--
-- Like refresh_tokens/password_reset_tokens: account-level, not tenant
-- data, so no RLS. One row per challenge token's jti: app.modules.identity
-- locks it, checks consumed_at/attempt_count, then either rejects the
-- attempt outright or lets it through and records the outcome, all before
-- verifying the code — see identity/repository.py's claim_mfa_attempt.
CREATE TABLE "mfa_challenge_attempts" (
    "id"            UUID NOT NULL DEFAULT gen_random_uuid(),
    "jti"           TEXT NOT NULL,
    "user_id"       UUID NOT NULL,
    "attempt_count" INTEGER NOT NULL DEFAULT 0,
    "consumed_at"   TIMESTAMPTZ(6),
    "expires_at"    TIMESTAMPTZ(6) NOT NULL,
    "created_at"    TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "mfa_challenge_attempts_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "mfa_challenge_attempts_jti_key" UNIQUE ("jti")
);
ALTER TABLE "mfa_challenge_attempts" ADD CONSTRAINT "mfa_challenge_attempts_user_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE;
CREATE INDEX "mfa_challenge_attempts_user_idx" ON "mfa_challenge_attempts" ("user_id", "created_at" DESC);
GRANT SELECT, INSERT, UPDATE, DELETE ON "mfa_challenge_attempts" TO app_tenant, app_platform;
