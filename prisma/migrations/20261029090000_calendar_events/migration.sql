-- Calendar support on events.
--
-- is_all_day: an all-day event covers whole days in the church's time zone. Its
-- start is that zone's midnight of the first day and its end the midnight after
-- the last day (the API normalises this). Existing events are all timed.
--
-- events_end_after_start: new and edited rows must end after they start. It is
-- added NOT VALID so it never rejects rows that were saved before this check
-- existed (an edit used to be able to produce an end before the start); it
-- applies to every insert and update from now on.
--
-- events_tenant_status_idx: calendar and review queries filter a church's events
-- by status.

ALTER TABLE "events" ADD COLUMN "is_all_day" BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE "events"
    ADD CONSTRAINT "events_end_after_start"
    CHECK ("end_date_time" IS NULL OR "end_date_time" > "start_date_time") NOT VALID;

CREATE INDEX "events_tenant_status_idx" ON "events" ("tenant_id", "status");
