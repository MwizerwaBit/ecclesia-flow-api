# Privacy and compliance (SA-15) — DRAFT, needs a qualified reviewer

This is an inventory of what the system does today, written so a qualified
privacy/legal reviewer has something concrete to check — it is **not**
itself a legal determination of lawful basis, applicable law, or
compliance. `FORYOU.md` already lists this as a decision only you (with
professional advice) can make: "which data-protection law applies where you
operate... lawful basis for member data, children's records, retention,
and cross-border hosting. This needs a qualified person, not me."

Related docs: [DATA_CLASSIFICATION.md](DATA_CLASSIFICATION.md) (what's
stored and where) · [THREAT_MODEL.md](THREAT_MODEL.md) ·
[INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md) §6 (breach notification —
same "needs a lawyer" caveat)

---

## 1. What this document is for

To let a reviewer answer, with confidence, questions like: what personal
data does this system hold, on whose behalf, under what basis, for how
long, and what happens when someone asks for it back or asks for it gone.
Everything below is a factual inventory; every **Open question** is a gap
this document cannot close on its own.

## 2. Who the data is about, and who controls it

- **Member/household data belongs to the church** that entered it
  (`Organization` is the tenant; `Member.tenant_id`). EcclesiaFlow operates
  the platform the church's data lives on — in typical data-protection
  terminology this makes the church the **data controller** and
  EcclesiaFlow the **data processor**, but that characterization itself
  needs legal confirmation, since it affects who owns which obligations.
- **Account data** (`User`) is held once per person per login, independent
  of which church(es) they belong to ([ADR-0005](adr/0005-persons-are-per-organisation.md)
  — though note a `Member` record is per-organisation; a `User` login can
  belong to several).
- **Open question:** does EcclesiaFlow need a data-processing agreement
  template for churches to accept, and does the product need an explicit
  controller/processor designation in its terms of service?

## 3. Lawful basis (inventory, not a determination)

| Category | Current mechanism | Open question |
|---|---|---|
| Member registration (self-service via `/auth/join`) | `members.consent_data_processing_at`, `consent_given_by`, `consent_communications` captured at join time (`app/modules/identity/service.py::join_church`) | Is explicit consent the right basis, or does "legitimate interest of the church" apply in the relevant jurisdiction(s)? Consent, once chosen, must be as easy to withdraw as to give — is there a withdrawal path today? |
| Member registration (added by staff, not self-service) | No explicit consent capture path visible in `members:create` — the person may not have interacted with the system at all | What basis applies when a church adds someone who hasn't consented directly (a common real-world case: a visitor's card is entered by staff)? |
| Pastoral notes | No separate consent — covered by the general member consent, if any | Pastoral/spiritual care records are often treated as a special category needing a higher bar — confirm whether that applies here |
| National ID / passport number | Required at `/auth/join` for self-registration (`app/core/pii.py`) | What's the specific lawful basis for collecting a government ID number, and is collecting it necessary and proportionate for every use case, or only some (e.g. required registration vs. a visitor record)? |
| Financial/giving records | Implied by the member relationship | Tax/financial record-keeping law may set its own retention floor, independent of privacy law's retention ceiling — these can conflict and need reconciling |
| Platform-level analytics/operational logs | `audit_logs`, access logs (`app/core/logging.py`) | Is "legitimate interest" (security, fraud prevention) sufficient basis for this, and does the retention rule in §5 match what that basis actually permits? |

## 4. Children's data

`/auth/join` requires the registrant to be an adult (per `plan.md`/the
registration flow's own rule), but **children's records can still exist**
in the system — entered by a parent, guardian, or church staff as ordinary
`Member` rows (there is no household-relationship-based consent model yet;
see the "family relationships" gap in `ARCHITECTURE.md` §6).

**Open question:** does a child's member record need stricter handling
(parental consent capture, restricted visibility, a lower retention ceiling)
than an adult's, and does the applicable law treat "a church recording a
baptism date for a minor" differently from "a platform processing a minor's
personal data" generally?

## 5. Retention

No automatic retention/deletion schedule exists for any category today —
data is kept until a church (or, via `data_erasure_requests`, a member)
actively asks for its removal, or a church closes and its data lifecycle
decision is made separately. See
[DATA_CLASSIFICATION.md](DATA_CLASSIFICATION.md) §7.

| Category | Current behaviour | Open question |
|---|---|---|
| Member/household/pastoral/sacramental records | Kept indefinitely while the church is active | What's an appropriate maximum retention period, if any, for an inactive member's record? |
| Financial records | Kept indefinitely | Does applicable tax/financial law set a *minimum* retention that could be shorter or longer than a privacy-driven maximum? |
| Audit log | Kept forever, by design (tamper-evidence depends on an unbroken chain — see [ADR-0007](adr/0007-tamper-evident-audit-log.md)) | Does an indefinite audit retention conflict with any data-minimisation obligation for the personal data *referenced inside* audit entries (e.g. `actor_user_id`, IP addresses)? |
| Backups | 30 days proposed (`OPERATIONS.md` §Backups, pending your confirmation) | Does a deleted member's data need to be purged from backups too, and on what timeline, once an erasure request is processed? |
| Session/credential data (refresh tokens, password reset tokens, MFA challenge attempts) | Revoked/expired per their own TTLs, rows not actively purged after expiry | Low sensitivity once expired, but an explicit cleanup job would be tidy — not a compliance blocker on its own |

## 6. Data subject rights

| Right | Current support |
|---|---|
| Access (see what's held) | A member can view their own profile/giving via the portal; no consolidated "export everything about me" endpoint exists |
| Erasure | `data_erasure_requests` table + a decision workflow (`DataErasureRequest`) — request-driven, reviewed by the church, not self-service or automatic |
| Portability | `member_transfers` moves a person's record between churches on the platform, with a configurable `history_scope` — this is **not** the same as exporting data to a format the person can take elsewhere entirely |
| Rectification | Ordinary `members:update` by staff; no self-service "request a correction" flow for a member who can't edit their own record directly |
| Object / restrict processing | Not implemented as a distinct mechanism — `directory_visible` is the closest existing opt-out, and it only covers directory display |

**Open question:** which of these rights are legally mandatory to offer,
in which jurisdictions, and does the current request-driven/manual erasure
flow meet the response-time requirements that apply?

## 7. Cross-border hosting

Hosting location is not yet decided (`FORYOU.md` — "Hosting... Where the
API, PostgreSQL, file storage and secrets will live"). This matters for
compliance specifically because:

- Some data-protection laws restrict or require safeguards for transferring
  personal data outside a given jurisdiction (e.g. outside the country or
  region where the data subject resides).
- A church's members may reside in a different country from where the
  church itself is based, and different still from wherever the hosting
  provider's data centers are.

**Open question:** once hosting is chosen, does the data residency it
implies satisfy the requirements of every jurisdiction EcclesiaFlow's
churches and their members are in?

## 8. Third parties with data access today

| Party | What they can access | Basis |
|---|---|---|
| Stripe (billing) | Payment details, via Stripe's own hosted flow — `BillingCheckoutSession`/`BillingPayment` store references, not card numbers | Processor relationship; confirm Stripe's own DPA covers this use |
| SMTP provider (once configured — currently unset, `FORYOU.md`) | Password-reset email content (name, reset link) | Processor relationship |
| EcclesiaFlow platform admins | Everything, via `app_platform`'s RLS bypass, gated by `require_platform_admin` and (for impersonation) an `ImpersonationSession` record | Operational necessity; impersonation strips sensitive permissions (`IMPERSONATION_RESTRICTED_PERMISSIONS` — confirm this still matches intent) |

## 9. What this document cannot do

Determine lawful basis, confirm which law applies, set a compliant
retention period, or certify that any of the above is sufficient. That
requires the qualified review `FORYOU.md` already asks for. This document's
job is to make that review fast by having the facts assembled in one place.
