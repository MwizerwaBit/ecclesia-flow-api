# Security verification matrix (SA-17)

Requirement → threat → mitigation → module → test → release gate, in one
table. The point is traceability: if a requirement has no test listed, that
is the next thing to fix, not a reason to assume it's covered. Rows mirror
`TODO.md`'s SA-* items; read this alongside
[THREAT_MODEL.md](THREAT_MODEL.md) for the attack-path detail behind each
"Threat" column, and [AUTHORIZATION_MATRIX.md](AUTHORIZATION_MATRIX.md) /
[DATA_CLASSIFICATION.md](DATA_CLASSIFICATION.md) for the permission/data
detail.

**Release gate legend:** CI job that must pass before merge
(`.github/workflows/ci.yml`) — `test` (full pytest suite, incl. security
tests, against migrations-from-scratch), `security` (pip-audit + bandit),
`secrets` (gitleaks), or **manual** (reviewed by a person, not a CI check
yet).

| # | Requirement | Threat if missing | Mitigation | Module | Test | Gate |
|---|---|---|---|---|---|---|
| 1 | Tenant isolation | Cross-tenant read/write ([THREAT_MODEL](THREAT_MODEL.md) §3.1) | JWT-only tenant id + RLS `tenant_isolation` on every tenant table | `app/core/deps.py`, `app/core/database.py`, every migration | `tests/test_security.py`, `tests/test_foundation.py` | `test` |
| 2 | Unit (branch) isolation — people | Cross-branch read/write of member data | `Scope` ABAC + RESTRICTIVE `unit_scope*` RLS on `members`/`pastoral_notes`/`sacramental_records`/`unit_memberships` | `app/core/authz.py`, `prisma/migrations/20261009090000_.../` | `tests/test_foundation.py::test_database_enforces_unit_scope_without_the_application` | `test` |
| 3 | Unit isolation — groups, events, attendance | Cross-branch read/write of group/event/attendance data | RESTRICTIVE `unit_scope*` RLS, asymmetric read (church-wide visible everywhere) vs write (whole-church only) | `prisma/migrations/20261019090000_unit_scope_groups_events_attendance/` | `tests/test_foundation.py::test_database_enforces_unit_scope_for_groups_events_and_attendance` | `test` |
| 4 | Unit isolation — giving | Cross-branch read of financial data | **Not implemented** — no `unit_id` column on giving tables | — | — | — (tracked gap, [THREAT_MODEL](THREAT_MODEL.md) §4) |
| 5 | RBAC: no privilege escalation | A role grants a permission it doesn't hold | `ensure_grantable()` | `app/core/authz.py` | `tests/test_security.py` | `test` |
| 6 | RBAC: leader-only permissions | Administrator self-promotes to leader-level capability | `LEADER_ONLY_PERMISSIONS`, never in the `board` role, only grantable by a holder | `app/modules/rbac/models.py` | `tests/test_security.py` | `test` |
| 7 | MFA-gated sensitive permissions | A stolen access token (without fresh MFA) exports data or changes org settings | `MFA_REQUIRED_PERMISSIONS` checked in `require_permission` | `app/core/permissions.py`, `app/core/deps.py` | `tests/test_security.py::test_sensitive_permissions_require_an_mfa_verified_session` | `test` |
| 8 | Session freshness | A suspended/role-changed user keeps acting on an old access token | `_ensure_session_current` re-checks the membership every request | `app/core/deps.py` | `tests/test_security.py` | `test` |
| 9 | Password policy + lockout | Credential stuffing / brute force | Argon2, per-IP rate limit, per-account lockout after `MAX_FAILED_LOGINS` | `app/modules/identity/service.py` | `tests/test_engineering.py::test_failed_logins_and_lockout_are_audited` | `test` |
| 10 | MFA challenge single-use | A leaked/overheard challenge token is retried until it succeeds | Per-`jti` claim + attempt cap, burned on success or exhaustion | `app/modules/identity/repository.py::claim_mfa_attempt`/`burn_mfa_attempt` | `tests/test_engineering.py::test_mfa_challenge_tokens_cannot_be_replayed_after_success`, `::test_mfa_challenge_attempts_are_capped_per_token` | `test` |
| 11 | Refresh-token theft detection | A stolen, already-rotated refresh token is replayed | Reuse revokes the whole token family, in its own surviving transaction | `app/modules/identity/service.py::refresh_session` | `tests/test_security.py` | `test` |
| 12 | Step-up for leadership transfer | A stolen session hands the church to an attacker-controlled account | `require_step_up` on initiate/approve/cancel + two-approver separation of duties | `app/modules/team/service.py` | integration test TBD — exercised manually today | **manual** |
| 13 | Account recovery is safe and non-enumerating | Account enumeration via password reset; token guessing/reuse | Identical response regardless of account existence, single-use SHA-256 token, revokes every session on completion | `app/modules/identity/recovery.py` | `tests/test_engineering.py::test_password_reset_is_single_use_and_ends_existing_sessions`, `::test_reset_links_expire_and_emails_are_capped_per_account` | `test` |
| 14 | Tamper-evident audit log | Evidence of an attack (or an insider) is erased | Hash chain, triggers block UPDATE/DELETE/TRUNCATE for every role, signed external checkpoints | `app/modules/audit/`, `ADR-0007` | `tests/test_engineering.py::test_audit_log_is_append_only_and_tampering_is_detected` | `test` |
| 15 | Failed logins are audited | No trace of a credential-stuffing attempt | `auth.login_failed`/`auth.account_locked` entries | `app/modules/identity/service.py::_fail_login` | `tests/test_engineering.py::test_failed_logins_and_lockout_are_audited` | `test` |
| 16 | Authorization failures are audited | No trace of a privilege-escalation probe | `authz.permission_denied` entries via `AuditTapMiddleware` | `app/core/middleware.py`, `app/main.py::_log_authorization_denied` | `tests/test_engineering.py::test_authorization_denials_are_audited` | `test` |
| 17 | Leadership-transfer audit trail | No record of who initiated/approved/completed a leadership change | `team.leadership_transfer_*` entries on every step | `app/modules/team/service.py` | covered indirectly by the leadership-transfer flow tests; no dedicated audit-content assertion yet | **manual** |
| 18 | Sensitive reads/exports are audited | No record of who read a pastoral note or exported the directory | **Not implemented** — only authorization *failures* are logged today, not successful sensitive reads | — | — | — (tracked gap) |
| 19 | Platform-admin actions are double-logged | A platform-admin action against a tenant is invisible to that tenant's own audit trail | Documented intent (tenant-level + platform-level entry); current coverage not re-verified this round | `app/modules/platform_admin/service.py` | not re-audited this round | **manual** |
| 20 | Malicious upload protection | Stored XSS via a disguised file; resource exhaustion | Byte-sniffed type check, allow-list, size cap, sandboxed CSP on the serving response | `app/core/private_storage.py` (or equivalent), `app/core/middleware.py::SecurityHeadersMiddleware` | covered under DIF-05 in `TODO.md` (fixed as a critical finding 2026-10-09) | `test` |
| 21 | Request-size limits | Oversized body exhausts memory/workers | Global body-size cap, 413 on chunked/declared overflow | `app/core/body_limit.py` | `tests/test_engineering.py::test_oversized_bodies_are_refused_with_413` | `test` |
| 22 | Rate limiting has a production-safe backend | Multi-worker production enforces `limit × workers`, not `limit` | `production_problems()` refuses to start with `RATE_LIMIT_STORAGE_URL=memory://` | `app/core/config.py`, `app/core/rate_limit.py` | no automated test of the startup refusal itself | **manual** |
| 23 | Secrets never reach production with dev values | A default/weak secret or password ships to production | `production_problems()` checked at startup for every secret/credential | `app/core/config.py` | exercised manually (`is_production=True` + dev values → refuses to start); no automated test | **manual** |
| 24 | Dependency and static-analysis scanning | A known-vulnerable dependency or an unsafe code pattern ships | pip-audit + bandit (medium+ severity/confidence) | CI | — | `security` |
| 25 | No secrets committed to history | A credential lands in git and is scraped | gitleaks on every push | CI | — | `secrets` |
| 26 | Hierarchy-change security | A branch admin keeps access to a unit after it moves elsewhere | Closure table follows moves; fixed as a critical finding 2026-10-09 (previously insert-only) | `app/modules/hierarchy/` | `tests/test_foundation.py` hierarchy tests | `test` |
| 27 | Cross-organisation affiliation doesn't leak | A parent organisation reads a child's raw records | `SECURITY DEFINER` functions gated by the child's own `grants`; parent never gets a tenant session on the child | `app/modules/affiliations/`, `ADR-0006` | `tests/test_foundation.py` affiliation tests | `test` |

## Rows needing attention

Rows marked **manual** have no automated test and depend on a person
re-checking them before each release that touches the relevant module.
Priority order to close them:

1. **#18** (sensitive-read auditing) and **#4** (giving unit-scope) are the
   two substantive gaps with no mitigation at all yet, not just missing
   tests — see [THREAT_MODEL.md](THREAT_MODEL.md) §4 for why each is
   deferred rather than built this round.
2. **#19** (platform-admin double-logging) should be re-verified against
   current code before the next platform-admin feature ships on top of it.
3. **#12** and **#17** have working, step-up-gated, audited implementations
   (verified manually this round) but no dedicated automated test
   asserting the audit *content* specifically — worth a follow-up test
   rather than a design change.
4. **#22**/**#23** are "does the app refuse to start" checks — a cheap unit
   test against `production_problems()` with monkeypatched settings would
   close both at once.

## Review

Re-derive this table (don't just re-read it) whenever a new SA-* item in
`TODO.md` changes status, a new module touches tenant/unit/financial data,
or a security test is added, renamed, or removed.
