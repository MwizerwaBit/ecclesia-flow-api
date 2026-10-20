# Incident response (SA-14) — DRAFT, needs your sign-off

This is a proposal, not an approved plan. Severity thresholds, the contact
list, and the notification obligations need a decision from you (and in the
case of breach notification law, a qualified legal opinion —
[PRIVACY_COMPLIANCE.md](PRIVACY_COMPLIANCE.md)) before this stops being a
draft. See [FORYOU.md](../../FORYOU.md) — "Review the architecture and
security docs."

Related docs: [THREAT_MODEL.md](THREAT_MODEL.md) (what we're defending
against) · [OPERATIONS.md](OPERATIONS.md) §Backups and recovery (the
restore mechanics this plan calls on) · [SECURITY_NOTES.md](SECURITY_NOTES.md)
(where lessons from a real incident should be recorded, the same way a bug
is recorded there today) · [SECURE_DEVELOPMENT.md](SECURE_DEVELOPMENT.md)
(vulnerability reporting intake)

---

## 1. Severity

| Severity | Definition | Example from this system |
|---|---|---|
| **SEV1 — Critical** | Active cross-tenant data exposure, credential compromise at scale, or the audit log's integrity is in question | A missed `tenant_id` check serving another church's members; the database owner's credentials are confirmed compromised |
| **SEV2 — High** | A single account or church compromised, contained to that scope | One church's leader account taken over via a phished password |
| **SEV3 — Medium** | A vulnerability found but not known to be exploited | A dependency with a known CVE, caught by `pip-audit`/Dependabot before an advisory confirms active exploitation |
| **SEV4 — Low** | A hardening gap with no known exploit path | A `manual`-gate row in [SECURITY_VERIFICATION_MATRIX.md](SECURITY_VERIFICATION_MATRIX.md) with no test yet |

**Proposed rule:** anything touching the categories in
[THREAT_MODEL.md](THREAT_MODEL.md) §1 (member PII, pastoral notes,
financial data, credentials, the audit log, platform-admin capability) is
SEV1 or SEV2 by default, never lower, until investigation proves otherwise.

## 2. Roles (placeholders — fill in before this is final)

| Role | Who | Does what |
|---|---|---|
| Incident commander | **[you]** | Declares severity, owns the decision to notify, calls it closed |
| Technical lead | **[you, or whoever is on call]** | Contains the issue, runs the restore/forward-fix |
| Communications | **[you]** | Drafts and sends any required notification |

A one-person team can hold all three roles; the table exists so a second
person added later knows what to pick up.

## 3. Detection

Today, detection is mostly reactive (someone notices, or a scheduled job
fails):

- `/health`/`/health/ready` and CI are the only automated signals that
  currently exist.
- `scripts/audit_checkpoint.py verify` catches audit-log tampering, but
  only when run — it isn't on a schedule yet (`OPERATIONS.md` §Backups).
- No error-tracking or anomaly-detection service is wired up (DIF-11,
  [FORYOU.md](../../FORYOU.md) — "Error tracking and metrics services").
- A vulnerability report from an external researcher
  (`docs/SECURE_DEVELOPMENT.md`'s intake process — needs **[security
  contact address]**) is itself a detection path.

**Gap:** there is no alerting for the specific abuse patterns
[THREAT_MODEL.md](THREAT_MODEL.md) describes (a spike in
`auth.login_failed`/`authz.permission_denied` entries, an unusual number of
`finance:export` calls, a platform-admin session active outside normal
hours). Until an error-tracking/metrics service is chosen, these are only
discoverable by querying `audit_logs` directly after the fact.

## 4. Containment

Immediate options available today, by scenario:

| Scenario | Containment action |
|---|---|
| One compromised account | Suspend the account (`users.status`), revoke all its refresh tokens (`repository.revoke_all_refresh_tokens`, same mechanism `recovery.py` uses after a password reset), force a password reset |
| One compromised church (leader account, say) | The above, for every account in that tenant's membership; consider suspending the organisation (`org/lifecycle.py` transitions) while the leader account is secured |
| A vulnerability in a dependency | Pin/patch and redeploy per `docs/SECURE_DEVELOPMENT.md`'s severity/remediation table |
| Suspected database-owner-level compromise | This is the scenario [ADR-0007](adr/0007-tamper-evident-audit-log.md) exists for: verify the audit chain and checkpoints before trusting anything else in the database; rotate `APP_TENANT_DB_PASSWORD`/`APP_PLATFORM_DB_PASSWORD` and the database owner's own credentials |
| A bug actively serving cross-tenant data | Roll back the release (forward-fix per `docs/MIGRATIONS.md`, never an untested down-migration); if the bug is in a `SECURITY DEFINER` function, revoke its `EXECUTE` grant immediately as a stopgap while the fix is prepared |

## 5. Eradication and recovery

1. Confirm the root cause (not just the symptom) before declaring it fixed
   — write the lesson to [SECURITY_NOTES.md](SECURITY_NOTES.md) regardless
   of severity, the same way existing entries there document bugs found and
   fixed.
2. If data was altered or lost: restore per `OPERATIONS.md` §Backups
   (**RPO 15 min / RTO 4 h, proposed** — confirm in
   [FORYOU.md](../../FORYOU.md)). Reconcile anything written since the
   restore point by hand; this is why destructive migration steps are
   isolated from the releases that stop needing the old structure
   (`docs/MIGRATIONS.md`).
3. Verify the audit chain (`verify_audit_chain`/`chain_breaks`) for every
   affected tenant before and after recovery, so the incident record itself
   isn't in question.
4. Re-run the full test suite and a fresh-database migration check
   (`docs/MIGRATIONS.md` testing section) before resuming normal releases.

## 6. Notification — **needs a qualified legal opinion, not just this doc**

Whether and when affected churches (and, depending on jurisdiction, a
regulator) must be told is a legal question this document cannot answer.
Proposed defaults, pending that review:

- Notify an affected church's leader directly, with what was affected, when
  it happened, when it was found, and what was done — regardless of legal
  obligation, because trust is the product.
- Do not notify before containment is confirmed, to avoid a second wave of
  attempts against an issue that's only partially closed.
- Track the applicable law per [PRIVACY_COMPLIANCE.md](PRIVACY_COMPLIANCE.md)
  once that review happens (timelines for mandatory breach notification
  vary significantly by jurisdiction).

## 7. Post-incident review

For every SEV1/SEV2, within a week of closing it:

- What happened, in the order it happened (timeline).
- What the actual root cause was (not the first symptom noticed).
- What already-existing control should have caught it, and why it didn't —
  or confirm no existing control was supposed to.
- One concrete follow-up per gap found, added to `TODO.md` or
  [SECURITY_VERIFICATION_MATRIX.md](SECURITY_VERIFICATION_MATRIX.md) with an
  owner.
- A short entry in [SECURITY_NOTES.md](SECURITY_NOTES.md), the same place
  smaller bugs already get recorded, so the next person (including future
  you) doesn't have to rediscover it.

## 8. What this draft is missing

- A real on-call rotation and paging mechanism (needs more than one
  person).
- An actual communications template (holding statement, customer-facing
  notice).
- A tested runbook — this has not been rehearsed against a real or
  simulated incident yet. `FORYOU.md`'s "first release rehearsal on
  staging" is a lighter-weight relative of this; a dedicated incident
  drill is a separate exercise.
- Confirmed severity thresholds and the legal notification review (§6).
