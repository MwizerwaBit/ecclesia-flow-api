-- Reviewing gatherings is an administrator/leader duty (or a custom role the
-- leader grants it to). Staff submit gatherings for review rather than
-- approving their own.
DELETE FROM "role_permissions"
WHERE "role_id" = '00000000-0000-0000-0000-000000000002' AND "permission" = 'events:review';
