# Operations runbook

Environments, secrets, observability, releases and backups (DIF-10 to
DIF-13). Steps that depend on the hosting provider are marked **[host]**.
Choosing one is a decision in FORYOU.md. Until then, these are the
requirements any setup must meet.

## Environments

| | development | test | staging | production |
|---|---|---|---|---|
| Purpose | a developer's machine | CI, one throwaway DB per run | production-like rehearsal | real churches |
| `ENVIRONMENT` | `development` | `test` | `staging` | `production` |
| Database | local PostgreSQL | CI service container | own instance | own instance, backups on |
| Data | fake / seed | created by tests | anonymised or fake only — **never a copy of real personal data** | real |
| Email | `dev_outbox/` files | in-memory outbox | SMTP to a test inbox | SMTP |
| Payments | demo provider | demo provider | Stripe test keys | Stripe live keys |
| Secrets | `.env` (git-ignored) | workflow env (throwaway values) | secret manager **[host]** | secret manager **[host]** |
| API docs (`/docs`) | on | on | on | off |

Each environment has its own database, its own secrets, and its own
database role passwords. Nothing is shared between staging and production.

## Configuration and secrets

- Every setting is a variable read by `app/core/config.py`. `.env.example`
  lists them all.
- **Production refuses to start** while any development value remains
  (`Settings.production_problems()`): development JWT, MFA or PII keys,
  default database role passwords, missing Stripe keys, missing SMTP, or
  `DEV_AUTO_ACTIVATE_ORGS`.
- Secrets live in the host's secret manager **[host]**, injected as
  environment variables. They are never in git, never in images, and never
  in logs (the log scrubber redacts tokens, keys, emails and sensitive
  fields; see below).
- The database role passwords in the first migration (`change_me_*`) are
  placeholders. Before production, run
  `ALTER ROLE app_tenant PASSWORD '…'` and the same for `app_platform`, and set
  the matching `APP_*_DB_PASSWORD` and `DATABASE_URL_*`.
- `AUDIT_CHECKPOINT_KEY` is held **outside** the app's environment, by
  whoever runs `scripts/audit_checkpoint.py`.

### Rotation

| Secret | How to rotate | Effect |
|---|---|---|
| `JWT_SECRET` | set the new value and restart | all access tokens are invalid at once; users refresh (refresh tokens are opaque database rows, unaffected) |
| DB role passwords | `ALTER ROLE … PASSWORD`, update the env, rolling restart | none if done in that order |
| Stripe / SMTP keys | create the new key at the provider, update the env, restart, revoke the old key | none |
| `MFA_ENCRYPTION_KEY`, `PII_ENCRYPTION_KEY` | **needs a re-encryption job (not built yet, TODO.md SA-11).** Don't rotate until it exists | rotating without it makes stored MFA secrets and ID numbers unreadable |
| `AUDIT_CHECKPOINT_KEY` | create a checkpoint with the old key, then start a new series with the new key; keep the old key to verify old checkpoints | none |

Rotate immediately if a secret might have been exposed. Otherwise rotate at
least yearly, and whenever someone with access leaves.

## Observability

- **Logs:** one JSON object per line on stdout (`app/core/logging.py`).
  Request bodies are never logged. Query strings are logged as keys only.
  Tokens, JWTs, keys, passwords and sensitive fields are redacted, and emails
  are reduced to their domain. Each request writes one `ecclesia_flow.access`
  line with method, path, status, duration and `request_id`. Ship stdout to
  the host's log service **[host]**.
- **Health:** `GET /health` checks liveness (the process is up).
  `GET /health/ready` checks readiness (the database answers) and returns
  `503` otherwise. Point the load balancer at `/health/ready`.
- **Error tracking:** not wired in yet. Choose a service (FORYOU.md), then add
  its SDK in `create_app` with the same scrubbing rules (TODO.md DIF-11).
- **Metrics:** not exported yet (TODO.md DIF-11). Minimum set once added:
  request rate, error rate and latency per route, database pool usage, failed
  logins and reset requests per hour.

## Reverse proxy

The proxy in front of the API **[host]** must:

- terminate TLS (TLS 1.2+, HSTS) and redirect HTTP to HTTPS
- cap request bodies at **11 MB** (nginx: `client_max_body_size 11m;`) so
  oversized uploads never reach a worker; the app enforces the same limits
  again (`app/core/body_limit.py`)
- pass `X-Request-Id` through if it sets one
- use a shared rate-limit store: set `RATE_LIMIT_STORAGE_URL` to Redis when
  running more than one worker

## Release

A release is a `vX.Y.Z` tag on `master` (`.github/workflows/release.yml`).
Code reaches `master` only through `development` → `staging` → `master`
pull requests ([CONTRIBUTING.md](CONTRIBUTING.md#branching-and-commits)). The
`staging` branch is what the staging environment runs **[host]**.

1. **Gate.** All CI checks re-run on the tagged commit: lint, formatting,
   migrations on a clean database, tests, dependency audit, static analysis
   and secret scan. Any failure stops the release.
2. **Approval.** The `production` environment requires an approving reviewer
   in GitHub before the release job runs (FORYOU.md).
3. **Staging first** **[host]**. The `staging` branch, at the commit being released, is deployed to staging; run migrations
   there, and smoke-test: sign in, list members, create a gathering,
   `/health/ready`.
4. **Back up production** and confirm the backup completed (see Backups).
5. **Migrate, then deploy.** Run `python -m prisma migrate deploy` against
   production *before* switching traffic to the new code. Expand/contract
   ([MIGRATIONS.md](MIGRATIONS.md)) means the old code still works on the new
   schema while it runs.
6. **Verify.** `/health/ready` passes on every instance. Error rate and
   latency hold steady for 15 minutes. Run the smoke test against production.
7. **Roll back** if verification fails: redeploy the previous tag. The
   schema stays (expand/contract keeps it compatible). If a migration
   itself was wrong, forward-fix it ([MIGRATIONS.md](MIGRATIONS.md)).
   Restoring the database is the last resort.

**Exception process.** An urgent security fix may skip the staging soak, but
never the CI gate or the approval. Record who approved and why in the
release notes, and review it afterwards.

## Backups and recovery

Requirements any host setup must meet **[host]**:

- **Automated** daily full backups plus continuous WAL archiving
  (point-in-time recovery), kept for 30 days.
- **Encrypted** at rest, in a separate account or region from the database,
  readable only by a dedicated backup role, not by the app's credentials.
- **Recovery objectives:** these are proposed, and need confirming in
  FORYOU.md. **RPO 15 minutes** (at most 15 minutes of writes lost) and
  **RTO 4 hours** (service back within 4 hours).
- **Restore test, monthly:** restore the latest backup to a scratch instance,
  then run `prisma migrate status` and
  `scripts/audit_checkpoint.py verify <latest checkpoint>`, and count rows
  in `organizations`, `members` and `audit_logs`. Record the date, duration
  and result. A backup that has never been restored doesn't count as a backup.
- **Audit checkpoints:** take one daily with
  `scripts/audit_checkpoint.py create`, and store it outside the database
  host, ideally in storage with object lock.
