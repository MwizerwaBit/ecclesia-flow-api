-- A church has exactly one leader at a time. Leadership transfer clears the
-- outgoing flag before setting the incoming one, inside one transaction.
CREATE UNIQUE INDEX "tenant_memberships_one_leader_key"
  ON "tenant_memberships" ("tenant_id") WHERE "is_leader";
