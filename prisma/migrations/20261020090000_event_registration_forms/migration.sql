-- Custom registration questions on events, plus the submissions they collect
-- from a public share link. Answers are keyed by the field's label at
-- submission time, not a field id, so editing a form later never corrupts
-- an already-collected response (see EventRegistrationField doc comment).
CREATE TABLE "event_registration_fields" (
    "id"         UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"  UUID NOT NULL,
    "event_id"   UUID NOT NULL,
    "label"      TEXT NOT NULL,
    "field_type" TEXT NOT NULL,
    "options"    TEXT[] NOT NULL DEFAULT '{}',
    "required"   BOOLEAN NOT NULL DEFAULT false,
    "position"   INTEGER NOT NULL,
    "created_at" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "event_registration_fields_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "event_registration_fields_type_check" CHECK ("field_type" IN ('text', 'single_choice', 'checkbox'))
);
ALTER TABLE "event_registration_fields" ADD CONSTRAINT "event_registration_fields_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "event_registration_fields" ADD CONSTRAINT "event_registration_fields_event_fkey" FOREIGN KEY ("event_id") REFERENCES "events"("id") ON DELETE CASCADE;
CREATE INDEX "event_registration_fields_event_idx" ON "event_registration_fields" ("event_id", "position");

CREATE TABLE "event_registrations" (
    "id"               UUID NOT NULL DEFAULT gen_random_uuid(),
    "tenant_id"        UUID NOT NULL,
    "event_id"         UUID NOT NULL,
    "respondent_name"  TEXT NOT NULL,
    "respondent_email" TEXT,
    "respondent_phone" TEXT,
    "answers"          JSONB NOT NULL DEFAULT '{}',
    "created_at"       TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "event_registrations_pkey" PRIMARY KEY ("id")
);
ALTER TABLE "event_registrations" ADD CONSTRAINT "event_registrations_tenant_fkey" FOREIGN KEY ("tenant_id") REFERENCES "organizations"("id") ON DELETE CASCADE;
ALTER TABLE "event_registrations" ADD CONSTRAINT "event_registrations_event_fkey" FOREIGN KEY ("event_id") REFERENCES "events"("id") ON DELETE CASCADE;
CREATE INDEX "event_registrations_event_idx" ON "event_registrations" ("event_id", "created_at");

-- ═══════════════════════════ Row-level security ═══════════════════════════
DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['event_registration_fields', 'event_registrations'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_tenant_id())', t);
  END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE, DELETE ON "event_registration_fields", "event_registrations" TO app_tenant, app_platform;
