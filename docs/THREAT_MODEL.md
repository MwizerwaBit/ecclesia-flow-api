# Threat model (SA-01)

What EcclesiaFlow protects, who might attack it, how, and what stops them.
This is a living document: add a row to §4 the day a new abuse case is
found, whether or not it's fixed yet.

Related docs: [ARCHITECTURE.md](ARCHITECTURE.md) (the isolation model this
threat model assumes) · [AUTHORIZATION_MATRIX.md](AUTHORIZATION_MATRIX.md) ·
[DATA_CLASSIFICATION.md](DATA_CLASSIFICATION.md) ·
[SECURITY_VERIFICATION_MATRIX.md](SECURITY_VERIFICATION_MATRIX.md) ·
[SECURE_DEVELOPMENT.md](SECURE_DEVELOPMENT.md) ·
[SECURITY_NOTES.md](SECURITY_NOTES.md) (specific bugs found and fixed) ·
[adr/](adr/)

---

## 1. Assets, ranked by what it costs someone if they're exposed or altered

1. **Member personal data** — names, contact details, date of birth,
   national ID (hashed + encrypted, [app/core/pii.py](../app/core/pii.py)),
   household and family context, directory visibility preferences.
2. **Pastoral notes and sacramental records** — the most sensitive category
   in the system by design: `pastoral_notes.is_private`, author-only
   visibility, RLS-protected ([DATA_CLASSIFICATION.md](DATA_CLASSIFICATION.md)).
3. **Financial data** — giving records, pledges, batch totals. Not
   individually catastrophic if leaked within a church (members often see
   their own giving), but a cross-tenant leak of another church's finances
   is a trust-ending event, and manipulation (inflating a batch, voiding a
   real donation) is theft.
4. **Credentials and session material** — password hashes (argon2), MFA
   secrets (encrypted, [app/core/security.py](../app/core/security.py)),
   refresh token families, step-up tokens, MFA challenge tokens.
5. **The audit log itself** — if an attacker (including a database owner)
   can silently rewrite it, every other control's evidence trail is
   worthless. See [ADR-0007](adr/0007-tamper-evident-audit-log.md).
6. **Platform-admin capability** — a compromised platform-admin account (or
   an impersonation session) can act across every tenant.
7. **Availability** — a credential-stuffing or scraping run that saturates
   workers or the database denies service to every church on the platform
   (shared infrastructure, [ADR-0001](adr/0001-modular-monolith-shared-postgres.md)).

## 2. Actors

| Actor | Can normally do | Why they might attack |
|---|---|---|
| **Anonymous / unauthenticated** | Register a church, look up a public church by slug, view published public events, request a password reset | Enumerate accounts/churches, brute-force credentials, scrape public data, abuse registration |
| **Member** (lowest role) | View their own profile, giving, groups, public/member-visible events | Escalate to staff/administrator capability; read another member's pastoral notes or giving |
| **Staff** | Manage members, groups, events, attendance, basic finance (per `PERMISSION_CATALOGUE`, [AUTHORIZATION_MATRIX.md](AUTHORIZATION_MATRIX.md)) | Read/export data outside their assigned branch; escalate to administrator |
| **Administrator (`board` role)** | Everything staff can, plus exports, roles, org settings, pastoral notes, billing | Take over church leadership without the transfer process; grant themselves a permission nobody checked |
| **Church leader** | Everything administrator can, plus `leadership:manage`, `admins:manage`, `affiliations:manage` | Hold the account hostage (no second admin), refuse a legitimate transfer |
| **A compromised account at any role** | Whatever that role could do, now in an attacker's hands | — (this is the scenario, not the actor's own goal) |
| **Platform admin** | Cross-tenant: view/activate/suspend organisations, impersonate, decide erasure requests | A compromised platform-admin account is the single highest-impact actor in the system |
| **The database owner / infrastructure operator** | Full `BYPASSRLS` access at the Postgres level | Insider risk, or a compromised ops credential; see the audit-log threat model (§3.6) |
| **Another organisation (a different church)** | Nothing by default; optionally an affiliation partner with explicit `grants` | Read or write another church's data via a missed tenant check |

## 3. Attack paths, per abuse case

### 3.1 Cross-tenant read or write

**Path:** an attacker with valid credentials at Church A crafts a request
referencing a record id that actually belongs to Church B (guessed,
enumerated, or leaked from another channel), hoping a missed `tenant_id`
check lets it through.

**Mitigation (two independent layers, by design):**
- **Application:** `tenant_id` is read only from the signed JWT, never from
  a client-supplied header/body/query param (`app/core/deps.py`,
  `get_tenant_db`). Every service function operates inside a transaction
  already scoped to that tenant.
- **Database:** every tenant table has `ENABLE ROW LEVEL SECURITY` plus a
  `tenant_isolation` policy (`tenant_id = current_tenant_id()`), enforced
  for the `app_tenant` role regardless of what the application layer does or
  forgets. Fail-closed: an unset `app.tenant_id` makes `current_tenant_id()`
  NULL, which matches nothing ([ADR-0003](adr/0003-rls-with-two-database-roles.md)).
- **Tested:** `tests/test_security.py`, `tests/test_foundation.py` exercise
  this directly at the database, bypassing the application entirely.

**Residual risk:** a raw SQL query written inside a module's
`repository.py` that forgets a `tenant_id` filter is still caught by RLS —
but a function marked `SECURITY DEFINER` that forgets its own scoping would
not be. See §4 and [ADR-0009](adr/0009-mvc-layering-and-boundary-tests.md)
for why `SECURITY DEFINER` functions are reviewed with extra care
(`docs/MIGRATIONS.md` review checklist).

### 3.2 Cross-branch (unit) read or write within the same tenant

**Path:** a branch-scoped staff member (their `tenant_memberships.unit_scope_id`
limits them to one hierarchy unit and its descendants) tries to read or
change a record belonging to a sibling or unrelated branch.

**Mitigation:**
- **Application:** `app/core/authz.py`'s `Scope` — `unit_visible`,
  `member_visible`, `shared_readable`, `unit_writable` — compares the
  session's unit set against the record's `unit_id`. Out-of-scope answers
  404, not 403, so a scoped user can't even learn a record exists
  ([ADR-0004](adr/0004-unit-scope-in-the-database.md)).
- **Database:** RESTRICTIVE RLS policies keyed on `app.unit_scope`
  (`unit_in_session_scope()`) on `members`, `pastoral_notes`,
  `sacramental_records`, `unit_memberships`, and — since this round of
  Phase 3 work — `groups`, `group_memberships`, `events`, `event_reviews`,
  `attendance_records`. Church-wide records (`unit_id IS NULL` on groups
  and events) are readable from any branch but writable only by a
  whole-church session, matching `Scope.shared_readable`/`unit_writable`
  exactly (`prisma/migrations/20261019090000_unit_scope_groups_events_attendance/`).
- **Not yet scoped:** giving (funds, donation batches, donations, pledges)
  has no `unit_id` column at all, so it is tenant-only today. See
  [DATA_CLASSIFICATION.md](DATA_CLASSIFICATION.md) and the gap table in
  [ARCHITECTURE.md](ARCHITECTURE.md) §6.
- **Tested:** `tests/test_foundation.py::test_database_enforces_unit_scope_for_groups_events_and_attendance`
  and the member-data equivalent in the same file.

### 3.3 Credential attacks (password guessing, MFA bypass, session theft)

**Path:** brute-forcing a password, replaying or extending a second-factor
challenge, or reusing a stolen refresh token after it's already been
rotated.

**Mitigation:**
- Argon2 password hashing, a documented password policy, per-IP rate
  limiting on `/auth/login`/`/auth/mfa/*` ([API_STANDARDS.md](API_STANDARDS.md)),
  and a per-account lockout after `MAX_FAILED_LOGINS` consecutive failures
  (`app/modules/identity/service.py::_fail_login`).
- MFA (TOTP + one-time backup codes); a stolen password alone doesn't grant
  a session.
- **MFA challenge tokens are single-use and capped**, not just
  short-lived: a per-`jti` row (`mfa_challenge_attempts`) rejects a token
  once it succeeds, or once `MFA_CHALLENGE_MAX_ATTEMPTS` wrong codes have
  been tried against it — closing the replay window that a bare 5-minute
  expiry alone left open (`app/modules/identity/repository.py::claim_mfa_attempt`,
  `prisma/migrations/20261019100000_mfa_challenge_single_use/`).
- Refresh token rotation with theft detection: reusing an already-rotated
  token revokes the whole token family, in its own transaction so the
  revocation survives even though the request that detected it then fails
  (`app/modules/identity/service.py::refresh_session`).
- Step-up tokens (`X-Step-Up-Token`) gate the highest-risk actions
  (leadership transfer, MFA disable) independently of how fresh the access
  token is.
- **Tested:** `tests/test_engineering.py::test_mfa_challenge_tokens_cannot_be_replayed_after_success`,
  `::test_mfa_challenge_attempts_are_capped_per_token`,
  `::test_failed_logins_and_lockout_are_audited`.

**Residual risk:** rate limiting's storage is in-process (`memory://`) by
default — fine for one dev worker, but with more than one production
worker each one enforces its own quota, so the effective limit is
`configured limit × worker count`. Production now refuses to start with
the default store (`app/core/config.py::production_problems`); an operator
must set `RATE_LIMIT_STORAGE_URL` to a shared Redis instance
([FORYOU.md](../../FORYOU.md)).

### 3.4 Privilege escalation

**Path:** a staff member grants themselves (or a custom role they create)
a permission they don't already hold; a branch-scoped session appoints
someone with whole-church (`unit_scope: "all"`) access.

**Mitigation:**
- `ensure_grantable()` (`app/core/authz.py`) refuses to let anyone grant a
  permission they don't themselves hold, whether through a custom role or a
  direct assignment.
- `Scope.assignable_unit()` refuses to let a branch-scoped session place a
  new or moved record outside its own branch, or hand out whole-church
  scope.
- `LEADER_ONLY_PERMISSIONS` (`leadership:manage`, `admins:manage`,
  `affiliations:manage`) are never part of the administrator role and, by
  the no-escalation rule above, only grantable by someone who already holds
  them — which in practice means only the leader.
- Appointing an administrator or leader goes through its own flow (org
  onboarding, leadership transfer), never the ordinary staff-invite
  endpoint (`app/modules/team/service.py::invite`).
- **Tested:** `tests/test_security.py` exercises the no-escalation guard
  directly.

### 3.5 Privileged-role and leadership-change abuse

**Path:** a stolen leader session transfers leadership to an attacker-held
account with no one else able to object; or an administrator unilaterally
declares themselves leader.

**Mitigation:**
- Leadership transfer requires: (a) `require_step_up` (fresh MFA) on
  initiate, approve, and cancel; (b) the outgoing leader to initiate it
  (nobody else can start a transfer away from a leader who hasn't agreed);
  (c) **two independent approvals** from other active team members before
  the role actually moves (`required_approvals = 2`,
  `app/modules/team/service.py::approve_leadership_transfer`) — a
  separation-of-duties control, not a single-actor action; (d) every step
  (request, approve, cancel, complete) is written to the audit log
  (`action="team.leadership_transfer_*"`).
- **Tested:** `tests/test_foundation.py`/`test_security.py` exercise the
  transfer flow; the audit entries are a direct consequence of this round's
  work (previously the flow had no audit trail at all — see
  [SECURITY_VERIFICATION_MATRIX.md](SECURITY_VERIFICATION_MATRIX.md)).

**Residual risk:** the two approvers are drawn from "every other active
team member" (`list_active_membership_ids_excluding`), not from a specific
higher-trust subset — a church with few staff may have limited genuine
separation. This matches the product's current size assumptions; revisit
if/when a formal admin tier below leader is introduced (see the "several
scoped roles per login" gap in [ARCHITECTURE.md](ARCHITECTURE.md) §6).

### 3.6 Audit log tampering (including by the database owner)

**Path:** an attacker who compromises an application role, or the database
owner/operator themselves, tries to edit or delete evidence of what they
did.

**Mitigation:**
- Neither `app_tenant` nor `app_platform` can `UPDATE`/`DELETE`/`TRUNCATE`
  `audit_logs` (`REVOKE ... FROM app_tenant, app_platform`); a trigger
  additionally refuses it for **every** role, including the table owner,
  unless the trigger is first explicitly disabled.
- Each row is hash-chained to the previous one per organisation
  (`chain_seq`, `prev_hash`, `row_hash`); `verify_audit_chain()` detects
  exactly where a chain breaks.
- Periodic signed checkpoints (`scripts/audit_checkpoint.py`,
  `AUDIT_CHECKPOINT_KEY` — never stored in the database or the app's
  environment) mean even someone who disables the trigger, rewrites a whole
  chain, and recomputes consistent hashes still can't reproduce a valid
  signature for data created before the last checkpoint, and can't forge
  a new one without the key ([ADR-0007](adr/0007-tamper-evident-audit-log.md)).
- **Tested:** `tests/test_engineering.py::test_audit_log_is_append_only_and_tampering_is_detected`
  — includes a test that deliberately disables the trigger as the database
  owner and confirms the chain-break is still detected.

**Residual risk:** checkpoints must actually be scheduled and stored
somewhere the database owner can't edit; this is operational, not code —
tracked in [FORYOU.md](../../FORYOU.md).

### 3.7 Sensitive-action visibility gaps (who did what, and who was refused)

**Path:** before this round of Phase 3 work, a failed login, a locked
account, and a 403 permission denial left no trace — useful both for an
attacker (no alarm raised) and for legitimate incident response (nothing to
investigate after the fact).

**Mitigation (new this round):**
- Every failed login attempt and every lockout is written to the
  account-level audit chain (`action="auth.login_failed"` /
  `"auth.account_locked"`, `tenant_id=None` since nobody has proven they
  are the account yet — `app/modules/identity/service.py::_fail_login`).
- Every `403 Forbidden` from `require_permission`/`require_platform_admin`
  is logged with the acting user, tenant, and the path/method attempted
  (`action="authz.permission_denied"`,
  `app/core/middleware.py::AuditTapMiddleware` + `app/main.py::_log_authorization_denied`).
  This deliberately lives outside `app/core` proper — core may not import a
  module ([ADR-0009](adr/0009-mvc-layering-and-boundary-tests.md)) — so the
  middleware only decides *when*, and `app.main` supplies *what to write*.
- **Tested:** `tests/test_engineering.py::test_failed_logins_and_lockout_are_audited`,
  `::test_authorization_denials_are_audited`.

**Residual risk:** exports and sensitive reads (e.g. reading a pastoral
note, exporting a member list) are not yet individually audited — only
their *authorization failures* are. See §4 and
[SECURITY_VERIFICATION_MATRIX.md](SECURITY_VERIFICATION_MATRIX.md).

### 3.8 Platform-admin compromise and impersonation abuse

**Path:** a compromised platform-admin account reads or modifies any
tenant's data, or starts an impersonation session to act as a church's
staff without consent.

**Mitigation:**
- Platform-admin routes require `require_platform_admin`
  (`app/core/deps.py`) on top of normal authentication.
- The `app_platform` database role bypasses RLS — every dependency that
  hands out a transaction on it also requires `require_platform_admin`
  upstream, and the caller is responsible for an explicit `audit_logs`
  write (`app/core/database.py::platform_session`).
- Impersonation sessions are their own table (`ImpersonationSession`),
  tying the action back to the platform admin who started it.
- **Not yet mitigated:** platform-admin actions are not universally
  double-logged (once at the tenant level, once at the platform level) the
  way the original design called for — confirm current coverage before
  relying on it; see [SECURITY_VERIFICATION_MATRIX.md](SECURITY_VERIFICATION_MATRIX.md).

### 3.9 Malicious or malformed file upload

**Path:** uploading an executable or HTML file disguised as an image to
have it served back from the API's own origin (stored-XSS-via-upload), or
an oversized file to exhaust disk/memory.

**Mitigation:** uploads are byte-sniffed (not trusted by filename
extension or client-supplied `Content-Type`), allow-listed, size-capped,
and served from a sandboxed response (`Content-Security-Policy: sandbox`,
`app/core/middleware.py::SecurityHeadersMiddleware`) — fixed as a critical
finding on 2026-10-09 per `TODO.md`'s "critical security flaws don't wait
for Phase 4" rule. Request bodies are capped globally regardless
(`app/core/body_limit.py`, DIF-05).

## 4. Known gaps (not yet mitigated)

These are tracked, not hidden. Cross-reference `TODO.md`'s Phase 3 section
for the authoritative status of each.

| Gap | Why it matters | Tracked as |
|---|---|---|
| No single written account-lifecycle spec (password policy, lockout, recovery, MFA all exist but aren't described in one place) | Harder to audit for completeness | SA-02 |
| No formal authorization matrix artifact before this round | Permission model existed only in code | SA-04 — closed by [AUTHORIZATION_MATRIX.md](AUTHORIZATION_MATRIX.md) |
| No per-table/per-operation RLS policy spec | Policies exist but aren't catalogued in one place | SA-05 |
| No data classification/retention rules per category before this round | Can't answer "how long do we keep X" with confidence | SA-08 — partially closed by [DATA_CLASSIFICATION.md](DATA_CLASSIFICATION.md) |
| No MFA/PII encryption-key rotation job | A compromised key can't be rotated without a migration | SA-11 |
| Exports and sensitive reads aren't individually audited | Only authorization *failures* are logged, not successful sensitive reads | SA-12 |
| No formal incident-response plan | Ad hoc response if a breach happens | SA-14 — drafted in [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md), needs your sign-off |
| No qualified privacy/legal review | Lawful basis, cross-border hosting, children's data unconfirmed | SA-15 — drafted in [PRIVACY_COMPLIANCE.md](PRIVACY_COMPLIANCE.md), needs a qualified reviewer |
| Giving has no unit-level RLS | A branch-scoped session's app-level filtering is the only protection for donation data | §3.2 above |
| Rate-limit storage is in-process by default | Multi-worker production needs Redis, now enforced at startup but not yet provisioned | §3.3 above, [FORYOU.md](../../FORYOU.md) |

## 5. Review

This document should be re-read whenever: a new module is added that holds
member, financial, or credential data; a new cross-tenant or cross-branch
code path is introduced (anything touching a `SECURITY DEFINER` function);
or an incident occurs (update §4 and add the lesson to
[SECURITY_NOTES.md](SECURITY_NOTES.md)). Owner sign-off: see
[FORYOU.md](../../FORYOU.md) — "Review the architecture and security docs."
