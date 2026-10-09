# ADR-0007: Audit log as per-organisation hash chains with external signed checkpoints

- **Status:** Accepted
- **Date:** 2026-10-09
- **Related:** migration 20261009120000, app/modules/audit/integrity.py, scripts/audit_checkpoint.py, TODO.md DIF-08 / SA-12

## Context

`audit_logs` could be altered by any role with write access, including the
app's. The brief requires that audit history can't be silently altered, and
that integrity can be verified.

## Decision

1. The app roles lose `UPDATE`, `DELETE` and `TRUNCATE` on the table.
2. Triggers refuse update, delete and truncate for everyone, including the
   owner, unless they are deliberately disabled.
3. A `BEFORE INSERT` trigger chains each row per organisation:
   `chain_seq`, `prev_hash`, and
   `row_hash = sha256(prev_hash | row content)`. Account-level events form
   their own chain. An advisory lock per chain prevents forks.
4. `verify_audit_chain()` recomputes a chain and reports the first break.
5. `scripts/audit_checkpoint.py` HMAC-signs every chain head with a key
   that is never stored in the database or the app's environment.
   Verification checks both that each chain is consistent and that every
   checkpointed entry is unchanged.

## Alternatives considered

- **An external immutable sink** (cloud object lock, or a managed ledger
  service). Stronger, but depends on hosting choices not yet made
  (FORYOU.md). It can be added on top later: ship each row, or each
  checkpoint, there.
- **Append-only privileges without hashing.** Doesn't detect an owner-level
  rewrite.

## Consequences

- Inserts take a short per-organisation lock, so audit writes for one church
  are serialised. That's fine at expected volumes.
- The database owner can still rewrite a chain, but checkpoints that were
  stored elsewhere expose it. This depends on checkpoints actually being
  taken and stored off-host (FORYOU.md).
- Erasure requests can't delete audit rows. Personal data must not be put in
  audit metadata beyond ids (SA-15).

## Revisit when

A hosting provider with object lock is chosen. Ship checkpoints, or rows,
there automatically.
