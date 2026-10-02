# Security notes — what this build pass actually found

Written for whoever picks this up next. Each of these was caught by driving the
real API against a real Postgres instance (not just reading the code), and each
would have shipped a real vulnerability or a crash loop if left as first written.

## 1. `set_config(name, value, true)` does not reset to NULL after commit

This is the one that matters most if you touch RLS policies.

The whole tenant-isolation model depends on `app.tenant_id` being either (a) the
correct tenant for this request, or (b) completely absent, so that
`tenant_id = current_setting('app.tenant_id', true)::uuid` safely matches nothing.
The assumption was: `set_config(..., true)` ("SET LOCAL" semantics) scopes the
value to the current transaction, so once that transaction commits, the next
transaction on a pooled connection sees it as unset again.

**That's wrong for a custom GUC's first use on a connection.** Empirically:

```sql
begin;
select set_config('app.tenant_id', '', true);
commit;
begin;
select current_setting('app.tenant_id', true) is null;  -- returns FALSE, not TRUE
```

Once a custom GUC has been touched at all on a session, its post-commit baseline
is an **empty string**, not NULL. On a connection pool, that means: tenant A's
request sets `app.tenant_id`, commits: the next request on that same physical
connection — even an entirely unrelated, un-scoped one — inherits `''`, and
`''::uuid` is a hard cast error, not a harmless non-match.

**Fix:** every policy calls `current_tenant_id()` / `current_app_user_id()`
(defined in `alembic/versions/c7dc285932d3_security_and_seed.py`), two tiny SQL
functions that wrap `nullif(current_setting(...), '')::uuid`. Never write a raw
`current_setting('app.tenant_id', true)::uuid` in a new policy — it will work
fine in isolation and then fail intermittently under connection pooling.

## 2. Revoking a refresh-token family and then raising loses the revocation

`refresh_session`'s reuse-detection path used to do:

```python
await repository.revoke_refresh_token_family(db, row.family_id)
raise UnauthorizedError("Refresh token reuse detected...")
```

The request-scoped session is wrapped in `async with session.begin(): yield
session` (see `app/core/database.py`). Raising an exception *inside* that block
means the context manager sees an exception on exit and **rolls back**, which
silently undoes the revocation UPDATE that was supposed to burn the stolen token
chain. The client got the correct-looking error message; the database didn't
actually revoke anything, and the "burned" tokens kept working.

**Fix:** `await db.commit()` immediately after the revoke, before raising. Any
future "detect and punish" code path (lockouts, abuse flags, etc.) needs the same
pattern — a write that must survive an error response has to commit before that
response's exception is raised, not after.

## 3. A custom Pydantic validator's `ValueError` crashes the validation handler

The password-strength check (`RegisterRequest.password_strength`) raises
`ValueError(...)` on a weak password, which is exactly what Pydantic expects. But
`RequestValidationError.errors()` embeds that original exception object inside
each error's `ctx` dict, and plain `json.dumps` can't serialize an exception —
so the intended 422 response itself raised `TypeError`, which the generic
exception handler then turned into a 500. **Every weak-password registration
attempt was returning "Something went wrong on our end" instead of a validation
message.**

Fix: `app/core/exceptions.py`'s validation handler runs `exc.errors()` through
FastAPI's `jsonable_encoder` before returning it. Any handler that touches
`exc.errors()` directly needs the same treatment.

## 4. Backup codes were checked but never consumed

The first version of `_verify_code_or_backup` correctly hashed the submitted
code and checked membership in `user.mfa_backup_codes`, but never removed it on
success — so a single backup code worked indefinitely, defeating the entire
point of a *backup* code (one-time use in case the authenticator device is
unavailable). Fixed by removing the matched hash from the stored array and
persisting that immediately on the same request that verified it
(`app/modules/identity/service.py::_verify_code_or_backup`).

## Known, deliberately deferred

- **MFA challenge tokens are not single-use.** A `challenge_token` (issued after
  a correct password, before MFA) is a short-lived (5 min) stateless JWT with no
  server-side revocation list, so it could in principle be replayed multiple
  times within its window — each replay still requires a *valid* MFA code or
  backup code, so the practical impact is "mint another session," not "bypass
  MFA." Closing this needs a JTI-based used-token store (Redis set or a DB
  table); not implemented yet.
- **Rate limiting is in-memory (`slowapi`'s default storage).** Fine for a single
  dev process; a multi-worker production deployment needs a shared backend
  (Redis) via `RATE_LIMIT_STORAGE_URL`, or limits reset per-worker and are
  trivially bypassed by hitting a different worker.
