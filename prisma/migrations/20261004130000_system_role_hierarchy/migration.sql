-- System roles form a strict hierarchy: member ⊂ staff ⊂ board.
-- Staff and board are congregants too (their own giving, their own
-- notifications), and the no-escalation rule (app.core.authz.ensure_grantable)
-- needs board to hold everything staff has so a board member can invite staff.
-- Mirrors app/modules/rbac/models.py and src/hooks/useRole.ts.
INSERT INTO "role_permissions" ("role_id", "permission") VALUES
  ('00000000-0000-0000-0000-000000000002', 'giving:read_own'),
  ('00000000-0000-0000-0000-000000000002', 'notifications:read'),
  ('00000000-0000-0000-0000-000000000003', 'portal:view'),
  ('00000000-0000-0000-0000-000000000003', 'giving:read_own'),
  ('00000000-0000-0000-0000-000000000003', 'notifications:read')
ON CONFLICT DO NOTHING;
