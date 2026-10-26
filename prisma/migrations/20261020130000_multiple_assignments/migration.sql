-- Several scoped roles per login in one church.
--
-- A login used to hold exactly one membership row per church. A person can now
-- hold several "assignments" there, each a role over a unit scope (a youth
-- pastor for the Youth ministry who is also a finance clerk for the whole
-- church). Each assignment is its own tenant_memberships row, so every existing
-- rule keeps working per row: a session carries ONE active assignment (role,
-- permissions, single-valued unit scope), the live check in deps.py validates
-- that row, and RLS keeps reading one scope. Moving between a person's
-- assignments is a token switch, never a union of scopes.
--
-- What still holds: one primary membership per login, one leader per church
-- (both are separate partial unique indexes and are untouched).

DROP INDEX IF EXISTS "tenant_memberships_user_id_tenant_id_key";

-- The same role over the same scope can't be given twice. NULL scope means
-- "whole church", so it is folded to a fixed value for uniqueness.
CREATE UNIQUE INDEX "tenant_memberships_assignment_key"
  ON "tenant_memberships" ("user_id", "tenant_id", "role_id",
                           COALESCE("unit_scope_id", '00000000-0000-0000-0000-000000000000'::uuid));
