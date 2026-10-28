-- How long a leader-side session (leader, administrators, staff, custom roles)
-- may sit idle before the app signs it out. Church administrators choose it in
-- Settings; members always get an hour and platform admins fifteen minutes, so
-- neither has a column. Default 15 minutes, never under 5 or over 2 hours.

ALTER TABLE "organizations"
    ADD COLUMN "session_idle_minutes" INTEGER NOT NULL DEFAULT 15,
    ADD CONSTRAINT "organizations_session_idle_minutes_check" CHECK ("session_idle_minutes" BETWEEN 5 AND 120);
