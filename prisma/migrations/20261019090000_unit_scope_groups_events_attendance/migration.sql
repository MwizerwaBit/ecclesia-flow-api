-- Unit-scoped RLS for groups, events and attendance (Phase 3 / SA-05).
--
-- Mirrors the members/pastoral_notes/sacramental_records pattern from
-- 20261009090000_org_foundation_unit_scope_affiliations: RESTRICTIVE
-- policies AND with the existing permissive tenant_isolation policy, so
-- they only ever narrow what a session sees, never widen it.
--
-- groups and events both carry a nullable unit_id (NULL = church-wide).
-- app.core.authz.Scope already treats the two directions asymmetrically:
--   shared_readable(unit_id) = unit_id IS NULL OR unit_visible(unit_id)
--     -- a church-wide record is readable by every branch
--   unit_writable(unit_id)   = session is whole-church OR unit_id is in scope
--     -- only whole-church staff may create/change a church-wide record
-- unit_in_session_scope(NULL) already returns true exactly when the session
-- is whole-church (app.unit_scope = 'all') and false otherwise, which is
-- precisely unit_writable(NULL) — so the write policies below reuse it
-- unchanged. The read policies add the "unit_id IS NULL" escape hatch that
-- unit_writable does not have.
--
-- group_memberships, event_reviews and attendance_records have no unit_id
-- of their own. Their policy only checks that the parent row is visible via
-- an EXISTS subquery — the same trick pastoral_notes/sacramental_records
-- already use against members: Postgres applies the parent table's own RLS
-- policies to that subquery (same role, same session), so the child never
-- needs to repeat the scope logic. Checking existence (not writability) of
-- the parent for every command — not just SELECT — matches the existing
-- pastoral_notes/sacramental_records precedent: anyone who can see a
-- member may add a note or sacrament for them, not just that member's home
-- unit; likewise here, anyone who can see a group/event may record a
-- membership, review or attendance row against it.
--
-- Giving (funds, donation_batches, donations, pledges) is deliberately left
-- tenant-only: none of those tables has a unit_id column, and
-- donation_batches.service_id is optional, so there is no reliable branch
-- to scope a donation to without adding a column — a schema/product
-- decision, not something this migration should invent. See
-- docs/SECURITY_NOTES.md.

CREATE POLICY unit_scope_read ON "groups" AS RESTRICTIVE FOR SELECT
  USING (unit_id IS NULL OR unit_in_session_scope(unit_id));
CREATE POLICY unit_scope_insert ON "groups" AS RESTRICTIVE FOR INSERT
  WITH CHECK (unit_in_session_scope(unit_id));
CREATE POLICY unit_scope_update ON "groups" AS RESTRICTIVE FOR UPDATE
  USING (unit_in_session_scope(unit_id))
  WITH CHECK (unit_in_session_scope(unit_id));
CREATE POLICY unit_scope_delete ON "groups" AS RESTRICTIVE FOR DELETE
  USING (unit_in_session_scope(unit_id));

CREATE POLICY unit_scope ON "group_memberships" AS RESTRICTIVE
  USING (EXISTS (SELECT 1 FROM groups g WHERE g.id = group_memberships.group_id))
  WITH CHECK (EXISTS (SELECT 1 FROM groups g WHERE g.id = group_memberships.group_id));

CREATE POLICY unit_scope_read ON "events" AS RESTRICTIVE FOR SELECT
  USING (unit_id IS NULL OR unit_in_session_scope(unit_id));
CREATE POLICY unit_scope_insert ON "events" AS RESTRICTIVE FOR INSERT
  WITH CHECK (unit_in_session_scope(unit_id));
CREATE POLICY unit_scope_update ON "events" AS RESTRICTIVE FOR UPDATE
  USING (unit_in_session_scope(unit_id))
  WITH CHECK (unit_in_session_scope(unit_id));
CREATE POLICY unit_scope_delete ON "events" AS RESTRICTIVE FOR DELETE
  USING (unit_in_session_scope(unit_id));

CREATE POLICY unit_scope ON "event_reviews" AS RESTRICTIVE
  USING (EXISTS (SELECT 1 FROM events e WHERE e.id = event_reviews.event_id))
  WITH CHECK (EXISTS (SELECT 1 FROM events e WHERE e.id = event_reviews.event_id));

CREATE POLICY unit_scope ON "attendance_records" AS RESTRICTIVE
  USING (EXISTS (SELECT 1 FROM events e WHERE e.id = attendance_records.event_id))
  WITH CHECK (EXISTS (SELECT 1 FROM events e WHERE e.id = attendance_records.event_id));
