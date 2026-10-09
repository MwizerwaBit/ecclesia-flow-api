-- Engineering foundation (TODO.md Phase 4):
--   DIF-07  password reset tokens
--   DIF-08  tamper-evident, append-only audit log
--
-- Hand-written (docs/SECURITY_NOTES.md §6).

-- ═══════════════════════════ DIF-07 Password reset ═══════════════════════════
-- Like refresh_tokens: account-level, not tenant data, so no RLS. Only the
-- SHA-256 of a token is stored; the token itself exists only in the email.
CREATE TABLE "password_reset_tokens" (
    "id"           UUID NOT NULL DEFAULT gen_random_uuid(),
    "user_id"      UUID NOT NULL,
    "token_hash"   TEXT NOT NULL,
    "expires_at"   TIMESTAMPTZ(6) NOT NULL,
    "used_at"      TIMESTAMPTZ(6),
    "requested_ip" TEXT,
    "created_at"   TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "password_reset_tokens_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "password_reset_tokens_hash_key" UNIQUE ("token_hash")
);
ALTER TABLE "password_reset_tokens" ADD CONSTRAINT "password_reset_tokens_user_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE CASCADE;
CREATE INDEX "password_reset_tokens_user_idx" ON "password_reset_tokens" ("user_id", "created_at" DESC);
GRANT SELECT, INSERT, UPDATE, DELETE ON "password_reset_tokens" TO app_tenant, app_platform;

-- ═══════════════════════════ DIF-08 Audit integrity ═══════════════════════════
-- Each organisation's audit entries form a hash chain (platform/account
-- events, tenant_id NULL, form their own). Every row stores the previous
-- row's hash and its own hash over its content, so editing, deleting or
-- inserting a row anywhere in the past breaks every hash after it.
--
-- Protection, in layers:
--   1. The app roles can't UPDATE, DELETE or TRUNCATE audit_logs (privileges).
--   2. A trigger refuses UPDATE/DELETE for everyone, including the owner,
--      unless they deliberately disable it — which is itself visible.
--   3. verify_audit_chain() recomputes a chain and reports the first break.
--   4. scripts/audit_checkpoint.py signs each chain's head with a key that
--      is NOT in the database; a rewrite of the whole chain by someone with
--      owner access still fails against a checkpoint held elsewhere.

ALTER TABLE "audit_logs"
  ADD COLUMN "chain_seq"  BIGINT,
  ADD COLUMN "prev_hash"  TEXT,
  ADD COLUMN "row_hash"   TEXT;

CREATE OR REPLACE FUNCTION audit_row_content(a audit_logs) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT concat_ws('|',
    a.id::text,
    coalesce(a.tenant_id::text, ''),
    coalesce(a.actor_user_id::text, ''),
    a.action,
    a.resource_type,
    coalesce(a.resource_id::text, ''),
    coalesce(a.metadata::text, ''),
    coalesce(host(a.ip_address), ''),
    a.is_impersonated::text,
    coalesce(a.impersonated_by_user_id::text, ''),
    to_char(a.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
    a.chain_seq::text
  )
$$;

CREATE OR REPLACE FUNCTION audit_row_hash(p_prev text, a audit_logs) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT encode(sha256(convert_to(coalesce(p_prev, 'genesis') || '|' || audit_row_content(a), 'UTF8')), 'hex')
$$;

-- SECURITY DEFINER: the inserting session can't SELECT other organisations'
-- rows (RLS), but the chain head for its own chain must be found reliably.
CREATE OR REPLACE FUNCTION audit_logs_chain() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  v_prev_seq  bigint;
  v_prev_hash text;
BEGIN
  -- One writer per chain at a time, so sequence numbers never fork.
  PERFORM pg_advisory_xact_lock(hashtext('audit:' || coalesce(NEW.tenant_id::text, 'platform')));
  SELECT chain_seq, row_hash INTO v_prev_seq, v_prev_hash
    FROM audit_logs
   WHERE tenant_id IS NOT DISTINCT FROM NEW.tenant_id AND chain_seq IS NOT NULL
   ORDER BY chain_seq DESC
   LIMIT 1;
  NEW.created_at := coalesce(NEW.created_at, now());
  NEW.chain_seq := coalesce(v_prev_seq, 0) + 1;
  NEW.prev_hash := v_prev_hash;
  NEW.row_hash := audit_row_hash(v_prev_hash, NEW);
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_logs is append-only' USING ERRCODE = 'insufficient_privilege';
END;
$$;

-- Backfill the existing rows into chains, oldest first.
DO $$
DECLARE
  r        audit_logs%ROWTYPE;
  v_key    text := NULL;
  v_seq    bigint;
  v_prev   text;
BEGIN
  FOR r IN SELECT * FROM audit_logs ORDER BY coalesce(tenant_id::text, ''), created_at, id LOOP
    IF v_key IS DISTINCT FROM coalesce(r.tenant_id::text, '') THEN
      v_key := coalesce(r.tenant_id::text, '');
      v_seq := 0;
      v_prev := NULL;
    END IF;
    v_seq := v_seq + 1;
    r.chain_seq := v_seq;
    r.prev_hash := v_prev;
    r.row_hash := audit_row_hash(v_prev, r);
    UPDATE audit_logs SET chain_seq = r.chain_seq, prev_hash = r.prev_hash, row_hash = r.row_hash WHERE id = r.id;
    v_prev := r.row_hash;
  END LOOP;
END $$;

ALTER TABLE "audit_logs" ALTER COLUMN "chain_seq" SET NOT NULL, ALTER COLUMN "row_hash" SET NOT NULL;
CREATE UNIQUE INDEX "audit_logs_chain_idx" ON "audit_logs" (coalesce(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid), chain_seq);

CREATE TRIGGER audit_logs_chain_insert
BEFORE INSERT ON "audit_logs"
FOR EACH ROW EXECUTE FUNCTION audit_logs_chain();

CREATE TRIGGER audit_logs_no_update_delete
BEFORE UPDATE OR DELETE ON "audit_logs"
FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only();

CREATE TRIGGER audit_logs_no_truncate
BEFORE TRUNCATE ON "audit_logs"
FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_append_only();

REVOKE UPDATE, DELETE, TRUNCATE ON "audit_logs" FROM app_tenant, app_platform;

-- Account-level events (password resets, ...) have no organisation. A tenant
-- session may append them, but never read them back (the SELECT policy
-- stays tenant-only).
CREATE POLICY account_events_insert ON "audit_logs" FOR INSERT
  WITH CHECK (tenant_id IS NULL AND resource_type = 'user');

-- Recompute one chain. Returns nothing if intact, otherwise the first
-- sequence number whose stored hash or link doesn't match.
CREATE OR REPLACE FUNCTION verify_audit_chain(p_tenant_id uuid)
RETURNS TABLE (broken_at_seq bigint, reason text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE
  r          audit_logs%ROWTYPE;
  v_prev     text := NULL;
  v_expected bigint := 1;
BEGIN
  FOR r IN
    SELECT * FROM audit_logs WHERE tenant_id IS NOT DISTINCT FROM p_tenant_id ORDER BY chain_seq
  LOOP
    IF r.chain_seq <> v_expected THEN
      broken_at_seq := v_expected; reason := 'missing entry'; RETURN NEXT; RETURN;
    END IF;
    IF r.prev_hash IS DISTINCT FROM v_prev THEN
      broken_at_seq := r.chain_seq; reason := 'broken link'; RETURN NEXT; RETURN;
    END IF;
    IF r.row_hash <> audit_row_hash(v_prev, r) THEN
      broken_at_seq := r.chain_seq; reason := 'content changed'; RETURN NEXT; RETURN;
    END IF;
    v_prev := r.row_hash;
    v_expected := v_expected + 1;
  END LOOP;
END;
$$;

-- Each chain's head, for signing checkpoints outside the database.
CREATE OR REPLACE FUNCTION audit_chain_heads()
RETURNS TABLE (tenant_id uuid, chain_seq bigint, row_hash text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT DISTINCT ON (a.tenant_id) a.tenant_id, a.chain_seq, a.row_hash
    FROM audit_logs a
   ORDER BY a.tenant_id, a.chain_seq DESC
$$;

REVOKE ALL ON FUNCTION audit_logs_chain() FROM PUBLIC;
REVOKE ALL ON FUNCTION verify_audit_chain(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION audit_chain_heads() FROM PUBLIC;
-- Verification and checkpoints are platform operations.
GRANT EXECUTE ON FUNCTION verify_audit_chain(uuid) TO app_platform;
GRANT EXECUTE ON FUNCTION audit_chain_heads() TO app_platform;
