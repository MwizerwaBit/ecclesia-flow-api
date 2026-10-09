# API standards

Conventions every endpoint follows (DIF-15). Where code enforces a rule, the
enforcing module is named.

## Versioning and paths

- Every route lives under `/api/v1`. Breaking changes (removing a field,
  changing a type or meaning, or changing a status code clients depend on)
  need `/api/v2` for the affected routes. Adding optional fields or new
  endpoints is not breaking.
- Paths are plural nouns in kebab-case: `/members/{id}`,
  `/unit-memberships/{id}/suspend`. Actions that aren't CRUD are verbs under
  the resource: `/affiliations/{id}/accept`, `/members/{id}/move`.
- The API exposes business operations, not table CRUD. Clients never send
  `tenant_id`, `created_by` or role grants for the server to trust. Those
  come from the session.

## Requests

- JSON bodies, validated by Pydantic schemas (`app/modules/*/schemas.py`).
  Unknown fields are ignored.
- Size limits: 1 MB per body, 11 MB for `multipart/form-data` uploads
  (`app/core/body_limit.py`). Larger bodies get `413`.
- Uploaded files are typed by their bytes, never by name or claimed type
  (`app/core/private_storage.py`).
- Identifiers are UUIDs.

## Responses and status codes

| Code | When |
|---|---|
| 200 | Success with a body |
| 201 | Not used; creates return 200 with the created resource, for compatibility |
| 202 | Accepted, completes asynchronously (password-reset request) |
| 204 | Success, no body |
| 400 | A business rule refused the request (`AppError`), e.g. `invalid_parent` |
| 401 | Not signed in, token invalid or expired, session revoked or stale |
| 402 | Organisation not active yet (`org_inactive`) |
| 403 | Signed in but not allowed: missing permission, `mfa_required`, `out_of_scope` for writes |
| 404 | Doesn't exist **or** exists but is outside the caller's tenant or branch. Existence isn't revealed |
| 409 | Conflicts with current state: duplicate, already answered, `cycle` |
| 413 | Body or file too large |
| 415 | File type not accepted |
| 422 | Request didn't match the schema (field errors included) |
| 423 | Account locked (only shown to the real password holder) |
| 429 | Rate limited |
| 500 | Unexpected. No internals leaked; correlate with the request id |

## Error format

Every error, from every layer, uses one envelope (`app/core/exceptions.py`,
`app/core/body_limit.py`):

```json
{"error": {"code": "out_of_scope", "message": "Human-readable sentence.", "requestId": "…"}}
```

- `code` is stable and machine-readable. Clients branch on `code`, never on
  `message`.
- `422` responses add `"fields": [...]` with Pydantic's per-field errors.
- Stack traces, SQL and file paths never appear in responses.

## Request ids

- Every response carries `X-Request-Id`. A caller may send its own (up to
  64 characters of `[A-Za-z0-9._-]`); anything else is replaced.
- The same id appears in the error body and in the access log line.

## Collections: pagination, ordering, filtering, sorting

- Every `list[...]` endpoint is paginated (`app/core/pagination.py`,
  [ADR-0008](adr/0008-pagination-in-headers.md)):
  - `?limit=` default 100, maximum 500; `limit=501` gets `422`
  - `?offset=` default 0
  - response headers: `X-Total-Count`, `X-Page-Limit`, `X-Page-Offset`,
    `Link: <…>; rel="next", <…>; rel="prev"`
  - the body stays a JSON array
- **Ordering is deterministic.** Every list query's `ORDER BY` must end in a
  unique column, so pages never overlap or skip. Some existing queries
  order by a non-unique column only (a name, or a date) and still need an `id`
  tie-breaker. That work is tracked under TODO.md DIF-06.
- **Filtering** uses named query parameters for specific fields
  (`?status=active&unit_id=…`), plus `?search=` for free text where offered.
  Filters only narrow results. Access is decided by the session, never by a
  filter.
- **Sorting:** each endpoint has one documented default order. Where
  client-chosen sorting is added, use `?sort=field` or `?sort=-field`
  against an allow-list of fields. Arbitrary columns are never accepted.

## Authentication and sessions

- `Authorization: Bearer <access token>` (15 minutes). Browsers get the
  refresh token as an httpOnly, SameSite=Strict cookie and must send
  `X-Requested-With: XMLHttpRequest` to refresh. Native clients ask for body
  transport with `X-Refresh-Token-Transport: body`.
- Sensitive operations need an MFA-verified session (`mfa_required`) or a
  step-up token (`X-Step-Up-Token`).

## Integration boundaries

Outbound integrations sit behind small interfaces: billing providers
(`app/modules/billing`) and email (`app/core/mailer.py`). Inbound webhooks
verify signatures, check freshness, and are idempotent by event id. New
integrations follow the same pattern and never call providers from inside a
database transaction they could block.
