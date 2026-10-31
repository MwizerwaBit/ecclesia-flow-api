-- Finishes what 20261029090000_calendar_events started.
--
-- A CHECK added NOT VALID is still enforced on every later UPDATE of a row, so a
-- legacy event whose end was saved on or before its start (an edit used to be able to
-- do that) would have refused even a cancel or a review decision. Those rows are
-- repaired by dropping the impossible end (the event then simply has no end time),
-- and the constraint is validated so it holds for every row from here on.

UPDATE "events" SET "end_date_time" = NULL WHERE "end_date_time" IS NOT NULL AND "end_date_time" <= "start_date_time";

ALTER TABLE "events" VALIDATE CONSTRAINT "events_end_after_start";
