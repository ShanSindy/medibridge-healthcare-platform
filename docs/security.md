# Security and data handling

What security controls MediBridge implements today, and what it does not do. This page
describes the code; it is not a security assessment, penetration test or regulatory
review.

## Safety boundary

MediBridge is a portfolio and educational healthcare simulation intended exclusively for
synthetic data. It has not been certified for clinical use or assessed for HIPAA
compliance. Do not enter real patient information or deploy it for clinical operations.

All sample records in the repository, the frontend demo data and the documentation
examples are fictional. Use obviously fictional names and identifiers, and reserved
domains such as `example.org`, for any data you create.

## Authentication

- **Tokens.** `POST /auth/login` issues an HS256-signed JWT ([token_service.py](../token_service.py)).
  Verification requires the `sub`, `iat`, `nbf`, `exp` and `type` claims, accepts only
  `type: access` and a positive numeric subject, and allows 5 seconds of clock skew.
  The signing key comes from `JWT_SECRET_KEY` and must be at least 48 characters, or the
  application does not start. Tokens expire after `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`
  (default 30).
- **Passwords** are hashed with Argon2id using argon2-cffi's defaults
  ([security.py](../security.py)). There is no password-strength policy.
- **Unknown usernames.** Login runs a dummy Argon2 verification when the username does not
  exist, so that response times differ less between unknown users and wrong passwords.
  This reduces a timing signal; it does not prove that usernames cannot be enumerated.
  Both cases return the same `401` message.
- **Account status.** Inactive accounts cannot sign in, and every authenticated request
  reloads the user from the database: a deactivated account's existing token stops
  working immediately, and role changes take effect on the next request.
- **Transport.** The development server uses plain HTTP. Tokens are bearer tokens sent
  in the `Authorization` header; the dashboard keeps them in memory only.

## Authorization

Roles: `admin`, `clinician`, `scheduler`, `viewer` (also enforced by a database `CHECK`).
Routes check permissions mapped to roles in [permissions.py](../permissions.py):

| Permission | admin | clinician | scheduler | viewer |
|---|:-:|:-:|:-:|:-:|
| `patients:read` | ✓ | ✓ | ✓ | |
| `patients:write` | ✓ | ✓ | | |
| `providers:read` | ✓ | ✓ | ✓ | ✓ |
| `providers:write` | ✓ | | | |
| `appointments:read` | ✓ | ✓ | ✓ | |
| `appointments:write` | ✓ | | ✓ | |
| `audit:read` | ✓ | | | |
| `users:manage` | ✓ | | | |

`users:manage` is defined but no route uses it; there are no user-management endpoints.
Public routes: `/`, `/health`, `/fhir/metadata`, `/auth/login`. `/auth/me` requires any
valid token, and `/auth/admin-check` checks the `admin` role directly. The full
route-by-route table is in [api.md](api.md#operations-and-access). Authorization is
enforced by the backend; the dashboard does not hide features by role beyond showing the
backend's access-denied responses.

## Data protection in the application

- **SQL injection.** All values reach PostgreSQL as query parameters. Queries with
  optional filters assemble their `WHERE` clause only from fixed fragments in the code.
- **Input validation.** Pydantic models validate request bodies (lengths, enumerations,
  dates, timezone-aware appointment times). HL7 messages are validated before any write.
- **Database constraints** enforce referential integrity, uniqueness, allowed values and
  non-overlapping scheduled appointments; conflicts are reported as `404` or `409`
  without exposing database details.
- **Audit events.** REST patient create, update and delete write an `audit_logs` row in
  the same transaction as the change: if the audit insert fails, the change is rolled
  back. Each event records the acting user, action, resource type and ID, and outcome;
  no patient demographics are stored in it. Administrators read events through
  `GET /audit-logs`.
- **Secrets.** Configuration comes from `.env`, which is git-ignored; the repository
  contains only placeholder examples. Frontend `VITE_*` variables are compiled into the
  browser bundle and must never contain secrets.

## Development and test safeguards

- **Disposable test databases.** Each test run creates its own PostgreSQL database with
  an ownership marker, verifies its identity before writing, empties it around every
  test and drops it when the run finishes normally. Databases left by interrupted runs
  are reported by later runs, not deleted automatically. Existing databases are never used.
- **Fail-closed configuration.** Tests read only `.env.test`, which must declare
  `MEDIBRIDGE_ENV=test`; the development `.env` is never loaded. Superuser test roles
  are refused unless explicitly allowed in `.env.test`, and runs that bypass the safety
  setup are rejected. Inherited `PGHOSTADDR`, `PGSERVICE` and `PGOPTIONS`, which libpq
  would apply despite the explicit settings, stop the run, and the harness checks the
  address its create, clean and drop connections actually reached before using them. CI uses a dedicated
  non-superuser role.
- **Dependency audits.** CI runs `pip-audit --strict` and `npm audit` on every pull request
  and push to `main`.

Details: [development-and-testing.md](development-and-testing.md). A passing test suite
and clean audits show that known checks pass; they do not show that the application is
secure.

## Known limitations

These findings come from code review during development. The priorities are preliminary
engineering judgements, not the result of a formal security assessment.

| Finding | Preliminary priority |
|---|---|
| HL7 patient writes (A04 registrations, A08 updates) create no audit events | Medium |
| Provider create, update and delete create no audit events | Medium |
| Appointment create, update and cancel create no audit events | Medium |
| Reads of patient data (REST and FHIR) are not audited | Medium |
| Failed logins are not audited | Medium |
| No login rate limiting or account lockout | Medium |
| No refresh tokens and no token revocation list; tokens stay valid until they expire, unless the account is deactivated | Low |
| No password-strength policy for created users | Low |
| No application-level CORS configuration (the dashboard relies on the Vite development proxy) | Low |
| Connections from `database.get_connection()` have no application-configured timeout (existing PostgreSQL server or session settings may still apply), so requests can hang if PostgreSQL is unreachable (the shared engine, used by the audit-log, provider and appointment reads, has a connect timeout, a pool wait timeout and per-transaction statement and lock timeouts, but no network read timeout or keepalive, so a call can still hang if the server stops responding mid-query) | Low (reliability) |
| Outside the test suite, libpq environment variables such as `PGHOSTADDR`, `PGSERVICE` and `PGOPTIONS` still apply to the application's connections (both paths) and to `migrate.py`, `provision_user.py` and `seed_demo_data.py`; the process environment must be trusted | Low (operational) |
| Database error messages can contain data: the shared engine hides bound parameters from its errors and statement log, but not the SQL text or PostgreSQL's own messages (such as a duplicate key value); unhandled errors reach the server log | Low |
| `/health` reports liveness only; there is no database readiness check | Low (reliability) |
| No HTTPS in the development setup | Low (deployment) |
| Branch protection is not configured, so CI results are not required before merging | Low (process) |

Planned follow-up work is listed in the [roadmap](../README.md#roadmap).
