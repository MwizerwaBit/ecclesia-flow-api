# ADR-0008: Pagination metadata in headers; bodies stay arrays

- **Status:** Accepted
- **Date:** 2026-10-09
- **Related:** app/core/pagination.py, docs/API_STANDARDS.md, TODO.md DIF-06

## Context

All 54 collection endpoints returned complete, unbounded arrays. The frontend
already consumes these arrays when running against the real API, so wrapping
them in `{items, total}` would break every list screen at once.

## Decision

`limit` (default 100, maximum 500, larger values refused) and `offset` on
every `list[...]` endpoint. Metadata goes in `X-Total-Count`, `X-Page-Limit`,
`X-Page-Offset` and `Link` (`rel="next"` / `rel="prev"`), all exposed to the
browser through CORS. This is applied by `PaginatedRoute`, so a new list
endpoint can't forget it.

## Alternatives considered

- **An envelope body.** Clearer to read, but a breaking change for the
  frontend.
- **Cursor pagination everywhere.** Better for very large or fast-changing
  lists, but no current list needs it. It can be adopted per endpoint later
  behind the same headers.

## Consequences

- Clients that don't read headers see at most 100 rows. The frontend needs
  to follow `Link` on large lists (TODO.md).
- Pages are currently cut after the query runs. Pushing `LIMIT`/`OFFSET`
  into SQL for the biggest tables is an optimisation that doesn't change the
  contract.

## Revisit when

A list needs stable paging under concurrent inserts (switch it to cursors),
or the API gets external integrators who would prefer an envelope (a
`/api/v2` decision).
