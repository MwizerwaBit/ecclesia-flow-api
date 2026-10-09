# Secure development

How code security is checked, what fails a build, and how vulnerabilities are
handled (DIF-14, SA-16).

## Automated checks (CI, every pull request)

| Check | Tool | Fails the build when |
|---|---|---|
| Python dependencies | `pip-audit --skip-editable` | any known vulnerability with a fix available |
| Python static analysis | `bandit -r app scripts -ll -ii` | any finding of **medium or higher** severity **and** confidence |
| Frontend dependencies | `npm audit --omit=dev --audit-level=high` | any high or critical advisory in shipped (non-dev) packages |
| Secrets in git history | gitleaks | any detected secret |
| Lint and format | ruff, oxlint | any finding |
| Migrations | `prisma migrate deploy` on an empty database | any migration fails |
| Isolation and authorization | pytest (cross-tenant, cross-branch, RLS-direct tests) | any test fails |

Dependabot opens weekly update pull requests for pip and npm, and monthly ones
for GitHub Actions.

### Why bandit's threshold is medium/medium

Bandit's low-confidence `B608` findings flag every place SQL text is
assembled. All 12 current ones were reviewed on 2026-10-09. Each one joins
constant SQL fragments, and every value from a caller is a bound parameter.
The one value that had been interpolated (the giving report's
`date_trunc` unit) is now bound as well. A new `B608` at medium confidence
fails the build.

## Severity and remediation

| Severity | Examples | Fix within | Owner |
|---|---|---|---|
| Critical | cross-tenant data access, authentication bypass, exposed secrets, remote code execution | **immediately**, regardless of phase; hotfix release | whoever finds it raises it; the lead assigns it the same day |
| High | broken authorization inside a tenant, stored XSS, injection | 7 days | module owner |
| Medium | denial-of-service vectors, missing hardening | 30 days | module owner |
| Low | best-practice gaps | backlog | — |

Critical issues are fixed before anything else, not queued behind Phase 4
(TODO.md). Example: a media-upload flaw found on 2026-10-09, where any file
type was served from the API origin, was fixed in the same session.

## Exceptions

When a check fails and the fix isn't possible yet (for example, there is no
patched version), the exception is recorded in TODO.md under DIF-14. It
names the advisory, why it doesn't affect production or what mitigates it,
an owner, and a review date of 90 days or less. Open exceptions:

| Advisory | Package | Why accepted | Review by |
|---|---|---|---|
| GHSA-c475-qrg2-pj4r, GHSA-jmr9-qjv8-65gv, GHSA-7pqw-9j4j-h8q3 (high) | `basic-ftp`, `extract-zip` through `puppeteer-core` | dev-only UI-audit tooling, never shipped; the fix needs a breaking puppeteer upgrade | 2027-01-09 |
| react-router 6.x advisory (moderate) | `react-router-dom` | below the high threshold; fixing needs the v7 upgrade (TODO.md) | 2027-01-09 |

## Reporting a vulnerability

Report privately to the maintainers (address in FORYOU.md once set), not in a
public issue. The report is acknowledged within 2 working days and assessed
against the table above. The reporter is told when it is fixed.

## Code review for security

See [CONTRIBUTING.md](CONTRIBUTING.md#pull-requests-and-review). Changes to
authentication, authorization, RLS or migrations, audit, or billing need a
second, security-aware reviewer. The PR template's checklist covers
isolation, migrations, secrets and tests.
