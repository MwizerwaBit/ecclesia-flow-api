# ADR-0005: A person record belongs to one organisation; no global person directory

- **Status:** Accepted
- **Date:** 2026-10-09
- **Related:** TODO.md DA-04, SA-15; docs/ARCHITECTURE.md §6

## Context

The brief wants a person's history preserved across moves. It also warns that
a global person record shared between independently governed organisations
creates privacy and identity-matching risks.

## Decision

`members` rows are tenant-scoped. The same human in two churches is two
records. A login (`users`) links to at most one person per church through
`members.user_id`, which is unique per church. Moving between churches goes
through the explicit transfer workflow (`execute_member_transfer`), which copies
only what the transfer's history scope allows and closes the old memberships.

## Alternatives considered

- **A global person with per-organisation memberships.** Needs a lawful
  basis and consent model for cross-organisation sharing, plus
  identity-matching rules. It would also expose a directory across a
  denomination.

## Consequences

- Isolation and privacy are simple to reason about.
- Possible duplicates across churches are intentional. A future "link my
  records" feature would need explicit consent.

## Revisit when

Validated customer need (PD-03) for cross-church identity, with a privacy
review (SA-15).
