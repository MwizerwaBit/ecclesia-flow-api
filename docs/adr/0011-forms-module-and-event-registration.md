# ADR-0011: Forms: one module for questions and answers, events keep their own rules

- **Status:** Accepted
- **Date:** 2026-10-31
- **Related:** TODO.md "Forms & Registrations" F1-F6, ADR-0009, ADR-0010, `app/modules/forms/`

## Context

Event registration had its own questions and responses tables inside the events module, answers keyed by
the question's wording, and nothing else could reuse any of it. A church also needs forms that are not
about an event (a visitor card, a survey). Building a second copy for those would split validation, the
public link, the export and the access rules into two places that drift apart.

## Decision

- **A `forms` module owns definitions, questions and submissions.** Tables `forms`, `form_fields`,
  `form_submissions`. A form is either standalone or *owned*: `owner_kind` + `owner_id` is an opaque pointer
  (today only `event`) with no foreign key, so Forms never reads the owner's tables.
- **Events keeps what is the event's:** who may change or read its form (`events:update` and the event's
  branch), whether the event can take sign-ups (published and public), the URLs
  (`/events/{id}/fields`, `/responses`, `/churches/{slug}/events/{id}/register`), who is told. It reaches
  Forms only through `forms.service` (the `owned_*` functions) and `forms.schemas`; a boundary test fails on
  anything else. Forms imports nothing from Events, so there is no cycle. An owned form is invisible to the
  `/forms` API: to it, it does not exist.
- **A submission carries the form as the respondent saw it** (`form_snapshot`: title and questions), and the
  database refuses to change one: the app roles have no UPDATE or DELETE grant and a trigger rejects UPDATE
  for everyone. Editing or deleting a question later cannot change a past response. Answers stay keyed by the
  question's wording, which is what clients already send and read; question wording is therefore unique
  within a form.
- **Public submission** is validated on the server against the form's own questions (only its questions,
  only its options, the right shape, required means required, length caps) and rate limited. A standalone
  form's link is `/churches/{slug}/forms/{public_key}` with an unguessable key; drafts and event forms
  answer 404, a closed form answers "closed". A public submit returns a receipt, never the response.
- **Export is server side** (`.../submissions/export`, `.../responses/export`): every row up to 10 000, cells
  a spreadsheet would run as formulas are neutralised, and each export is audited (a count, never contents).
  Reading one response is audited too.
- **Event forms stay in the free tier** (`gatherings`); standalone forms are not tied to a paid module either,
  because registration is built on them.
- **Migration is expand/contract, in two steps:** structure first (0.2 s), then an idempotent copy of the old
  tables (ids kept, read-only on everything that exists), so the foreign-key locks are not held through the copy.
  The old tables are left unused for one release; a later migration drops them. Copied submissions are marked
  `reconstructed` because the questions as they were when sent were never kept. Because the previous release
  keeps writing the old tables until traffic switches, the copy doubles as a catch-up script run once after the
  switch, and a copy-back script serves an application rollback. Verified in
  [FORMS_MIGRATION_VERIFICATION.md](../FORMS_MIGRATION_VERIFICATION.md).

## Alternatives considered

- *A generic form engine (conditions, pages, uploads, payments):* deliberately not built.
- *Leave registration in events and add standalone forms beside it:* two copies of the same rules.
- *Events reading `form_submissions` directly for reminders:* couples the modules through tables; the reminder
  job asks `forms.service` for the respondent emails instead.
- *Answers keyed by question id:* every edit would orphan or misattribute answers, and existing responses and
  clients use wording.

## Consequences

- Easier: a new owner (a group sign-up) is a new `owner_kind` and a router that checks access, no new tables.
- Harder: a form's question wording must be unique, and renaming a question starts a new column in an export.
- Accepted: `answers` keys are typed by people, so the API client leaves that object's keys alone (it must not
  re-case them).

## Revisit when

A second owner needs different rules for who may read responses; exports grow past 10 000 rows (stream them);
or the legacy tables can be dropped.
