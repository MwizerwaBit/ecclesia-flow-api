-- Authentication hardening.
--
--  * Per-account lockout: rate limiting is per IP, so without this a password
--    can be guessed from many addresses at once.
--  * refresh_tokens.mfa_verified: the access token's mfa_verified claim must
--    survive rotation truthfully — a session that never passed MFA must not
--    become "MFA-verified" just by refreshing.
--  * Invitation tokens: invited staff had no password and no way to set one.
--    Only a SHA-256 hash of the token is stored, and it expires.

ALTER TABLE "users"
  ADD COLUMN "failed_login_count"   INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN "last_failed_login_at" TIMESTAMPTZ(6),
  ADD COLUMN "locked_until"         TIMESTAMPTZ(6),
  ADD COLUMN "password_changed_at"  TIMESTAMPTZ(6);

ALTER TABLE "refresh_tokens"
  ADD COLUMN "mfa_verified" BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE "tenant_memberships"
  ADD COLUMN "invite_token_hash" TEXT,
  ADD COLUMN "invite_expires_at" TIMESTAMPTZ(6);

CREATE UNIQUE INDEX "tenant_memberships_invite_token_hash_key"
  ON "tenant_memberships" ("invite_token_hash")
  WHERE "invite_token_hash" IS NOT NULL;
