# ADR-0010: Notifications: one writer, an outbox for email, reading always on

- **Status:** Accepted
- **Date:** 2026-10-29
- **Related:** TODO.md "Notifications module" N1-N10, ADR-0009, `app/modules/notifications/`

## Context

Several modules need to tell people things (event workflow, approvals, payments, reminders, an admin's own
message). Before this, one module wrote the `notifications` table directly and the rest had nothing. We need
duplicate prevention, recipient checks, content rules and email that can fail without hurting the request,
and the bell has to work for a church that has not paid, because "your payment was rejected" must reach it.

## Decision

- **One writer.** Only `notifications.service.notify` creates notifications. It checks recipients (active
  members of the caller's church), honours the in-app choice, dedupes on a per-trigger key, validates the
  link and text, and runs on the caller's transaction, so a rolled-back action leaves no notification. A
  boundary test fails if another module writes the table. Dependencies point one way: modules call the
  service; it calls none of them.
- **Email through an outbox.** `notify` writes a pending `notification_deliveries` row in the same
  transaction. A worker (`delivery.py`) sends from committed rows, retries with backoff, records scrubbed
  failures, and never touches the in-app row. Delivery is at-least-once.
- **Reading is always on.** `/notifications` and `/notification-preferences` sit in the always-on router group
  with their own permission, `notifications:read`: no paid module, no active-church check. Sending
  (`/notifications/send`) is a business feature in the `communications` module with `notifications:send`.
- **Polling, not push.** The app polls the unread count; there is no server-push infrastructure.

## Alternatives considered

- *Each module inserts its own rows:* no shared dedupe or checks; rejected.
- *Send email inline or in a request background task:* a crash or a provider outage loses or blocks mail, and
  an "after commit" hook has no retry; rejected for the outbox.
- *Gate reading behind `communications`:* a free or lapsed church would lose its bell; rejected.
- *WebSocket/SSE push:* needs a shared broker once there is more than one API instance; deferred.

## Consequences

- Easier: a new trigger is one `notify` call with a dedupe key; failures are visible per delivery.
- Harder: every notification type must be added in three places (migration CHECK, `NOTIFICATION_TYPES`, app
  type list); the unit test that compares the first two catches drift there.
- Accepted: email can very rarely arrive twice; the count is up to a minute stale; custom roles have no
  bell until the baseline-permission question is settled (TODO N5).

## Revisit when

A second API instance needs push; email volume needs digests or bounce handling; or the baseline permission
rules for custom roles change.
