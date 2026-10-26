-- Pastoral follow-up assignment on members (visitor follow-ups today).
-- Was entirely cosmetic before this: the API hardcoded assigned_to to null
-- and the frontend's "Reached" button only toggled local React state, never
-- calling the API — nothing here persisted. See docs/SECURITY_NOTES.md if a
-- similar "looks wired, isn't" gap turns up elsewhere.
ALTER TABLE "members" ADD COLUMN "followup_assigned_to_user_id" UUID;
ALTER TABLE "members" ADD COLUMN "followup_assigned_at" TIMESTAMPTZ(6);
ALTER TABLE "members" ADD COLUMN "followup_resolved_at" TIMESTAMPTZ(6);
ALTER TABLE "members" ADD COLUMN "followup_note" TEXT;
ALTER TABLE "members" ADD CONSTRAINT "members_followup_assignee_fkey" FOREIGN KEY ("followup_assigned_to_user_id") REFERENCES "users"("id") ON DELETE SET NULL;
CREATE INDEX "members_followup_assignee_idx" ON "members" ("followup_assigned_to_user_id") WHERE "followup_assigned_to_user_id" IS NOT NULL;
