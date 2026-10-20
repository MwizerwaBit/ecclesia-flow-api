# Data classification (SA-08)

What's stored, how sensitive each category is, how it's protected today,
and how long it's kept. Column lists are accurate as of this writing
(`prisma/schema.prisma`) — re-check them when a model changes rather than
trusting this file blindly.

Related docs: [THREAT_MODEL.md](THREAT_MODEL.md) ·
[AUTHORIZATION_MATRIX.md](AUTHORIZATION_MATRIX.md) ·
[PRIVACY_COMPLIANCE.md](PRIVACY_COMPLIANCE.md) (lawful basis, retention
obligations — needs a qualified reviewer) · [ADR-0005](adr/0005-persons-are-per-organisation.md)

---

## Classification levels

| Level | Meaning |
|---|---|
| **Restricted** | Government ID numbers, credentials, encryption/signing keys. Encrypted or hashed at rest; never returned by any API response. |
| **Confidential** | Pastoral notes, private records about a person that most roles in the same church can't see. |
| **Internal** | Ordinary member/financial data: visible within the church to roles with the matching permission, never across tenants. |
| **Internal (aggregate only)** | Visible to an affiliated parent organisation only as a count or published-events list — never the underlying record. |
| **Public** | Deliberately published: a church's public profile, published public events. |

## 1. Identity documents — **Restricted**

| Table.column | What | Protection |
|---|---|---|
| `members.national_id_hash` | HMAC-SHA256 of the normalised ID, keyed by `PII_ENCRYPTION_KEY` | Used only for the one-person-per-ID match; a keyed hash because ID numbers are short/structured enough that an unkeyed hash is brute-forceable |
| `members.national_id_encrypted` | Fernet ciphertext of the real number | Decrypted only for the rare authorised need to show it again; never returned by default |
| `members.national_id_last4` | Last 4 digits, plaintext | The only form any screen shows |
| `members.id_type` | `national_id` / `passport` / `other` | Not sensitive on its own |

**Key management:** `PII_ENCRYPTION_KEY` is separate from the JWT secret
and the MFA encryption key, so rotating one never touches the others
(`app/core/pii.py`). **Gap:** no re-encryption job exists yet, so rotating
this key in production would need a one-off migration script, not a
documented procedure (SA-11).

## 2. Credentials and session material — **Restricted**

| Table.column | What | Protection |
|---|---|---|
| `users.password_hash` | Argon2 hash | Never logged (`app/core/logging.py` scrubs it), never returned |
| `users.mfa_secret` | TOTP seed | Encrypted (`encrypt_mfa_secret`/`decrypt_mfa_secret`, `app/core/security.py`) |
| `users.mfa_backup_codes` | One-time backup codes | Hashed (`hash_backup_code`), consumed on use |
| `refresh_tokens.token_hash`, `password_reset_tokens.token_hash` | SHA-256 of the raw token | The raw token exists only in the client/email, never stored |
| `mfa_challenge_attempts.jti` | The challenge token's id, not the token itself | Not secret on its own — the token's signature is what proves authenticity; this table only tracks reuse, see [THREAT_MODEL.md](THREAT_MODEL.md) §3.3 |
| `AUDIT_CHECKPOINT_KEY`, `JWT_SECRET`, `APP_*_DB_PASSWORD` | Signing/encryption keys, DB role passwords | Environment only, never in the database or git; production refuses to start with a default value (`app/core/config.py::production_problems`) |

## 3. Pastoral and sacramental records — **Confidential**

| Table.column | What | Protection |
|---|---|---|
| `pastoral_notes.content` | Free-text notes about a member | `is_private` flag; RLS-scoped to the member it's about (inherits the member's visibility, [AUTHORIZATION_MATRIX.md](AUTHORIZATION_MATRIX.md) §3); gated by `pastoral_notes:read`/`write`, both **sensitive** permissions |
| `sacramental_records.*` | Baptism/sacrament type, date, officiant, location, notes | Same RLS inheritance as pastoral notes; not separately gated by a sensitive permission today beyond `members:read`-level access — **worth revisiting** whether this should require `pastoral_notes:read` instead |

**Retention:** no automatic deletion. A member's pastoral/sacramental
history follows them until an erasure request is processed (§7) or their
record is transferred to another church with `history_scope` set to
exclude it (`member_transfers.history_scope`).

## 4. Ordinary member and household data — **Internal**

| Table.column | What | Protection |
|---|---|---|
| `members.{first,last,preferred}_name`, `email`, `phone`, `whatsapp`, `photo_url` | Contact/identity | `members:read` |
| `members.date_of_birth`, `gender`, `marital_status`, `occupation`, `title`, `middle_name` | Demographic | `members:read` |
| `members.address_line1/2`, `city`, `state`, `country`, `postal_code` | Address | `members:read` |
| `members.emergency_contact_name/phone/relationship` | Emergency contact | `members:read` |
| `members.is_baptised`, `baptism_date` | A flag/date, distinct from the detailed `sacramental_records` row | `members:read` |
| `members.consent_data_processing_at`, `consent_given_by`, `consent_communications` | Consent capture | `members:read`; this is the record a privacy review (SA-15) would audit |
| `members.directory_visible` | The member's own opt-in/out of appearing in any member-facing directory | Enforced wherever a directory view is built — confirm current coverage before relying on it |
| `members.envelope_number` | Giving envelope number | Unique per church, looked up via `tenant_envelope_taken()`/`tenant_next_envelope_number()` (`SECURITY DEFINER`, scoped to `current_tenant_id()`) so a branch-scoped session never needs whole-church read access just to check it |
| `households.*` (name, address, `total_giving`) | Household grouping + aggregate | Same access level as the members in it |

## 5. Financial / giving data — **Internal**, not yet unit-scoped

| Table.column | What | Protection |
|---|---|---|
| `funds.*`, `donation_batches.*`, `donations.*` (amount, payment method, guest name, reference/envelope number), `pledges.*` | Giving records | `finance:*` permissions, all **sensitive** except `finance:create`; tenant-isolated by RLS, but **not** unit-scoped — see [THREAT_MODEL.md](THREAT_MODEL.md) §3.2 |
| `households.total_giving` | Aggregate, denormalized | Same as household data above |

A member sees their own giving (`giving:read_own`) regardless of role —
enforced by matching the record to the caller's own `user_id`, not by unit
scope.

## 6. Cross-organisation visibility — **Internal (aggregate only)**

| What | Protection |
|---|---|
| Affiliate aggregate stats, published-events list | `affiliate_summary()`/`affiliate_published_events()` — `SECURITY DEFINER` functions, the child organisation's own `grants` array decides what's exposed (`aggregate_stats`, `published_events`); the parent never receives a tenant session on the child, so it can never query the child's raw tables directly ([ADR-0006](adr/0006-consented-affiliations.md)) |

## 7. Erasure and portability

| Mechanism | What it does | Gap |
|---|---|---|
| `data_erasure_requests` table + `DataErasureRequest` model | Tracks a request to erase a member's data, referencing `member_id` | Decision/workflow exists; **no automated minimisation or retention schedule runs on a timer** — erasure is request-driven only |
| `member_transfers.history_scope` | `identity_only` / `include_sacraments` / `full_history` — controls what follows a member to a new church | Exists and is enforced in `execute_member_transfer()` |

**Known gap (SA-15):** there is no qualified legal review yet of which
data-protection law applies, lawful basis for each category above,
children's-data handling, or cross-border hosting constraints. See
[PRIVACY_COMPLIANCE.md](PRIVACY_COMPLIANCE.md) and
[FORYOU.md](../../FORYOU.md).

## 8. Audit log — special case

`audit_logs` rows contain `ip_address`, `actor_user_id`, and a free-form
`metadata` JSON column. Classify **whatever `metadata` is given** at the
sensitivity of the thing it describes — a rule, not currently enforced in
code, is that `metadata` should hold identifiers (ids, counts, paths), never
the sensitive content itself (e.g. log that a pastoral note was written,
never its text). The log itself is append-only and tamper-evident
([ADR-0007](adr/0007-tamper-evident-audit-log.md)) and has no retention
limit today (`THREAT_MODEL.md` §4).

## 9. Minimisation rules (what to apply when adding a new field)

- Never add a government ID field without hashing + encrypting it the same
  way `app/core/pii.py` does for national ID/passport — don't invent a
  second pattern.
- Never add a column that exists only to cache something derivable from an
  existing permission check — denormalized aggregates (`total_giving`) are
  the exception where performance already justified it, not the default.
- Any field a child organisation's affiliation `grants` could expose must
  go through a new `SECURITY DEFINER` function with an explicit allow-list,
  never a direct `grants`-gated table read (`ADR-0006`).
- Prefer a boolean/enum the member controls (like `directory_visible`) over
  a backend assumption, when the data is about visibility preference.
