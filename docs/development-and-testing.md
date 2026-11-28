# Development and testing

How to run MediBridge's backend tests, coverage, linting and dependency audits locally,
and what CI enforces. All data used by the tests is synthetic. To run the application
itself, see [getting-started.md](getting-started.md).

## One-time setup

Backend (from the repository root, Python 3.14):

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Test configuration: copy `.env.test.example` to `.env.test` and fill it in. It must contain
`MEDIBRIDGE_ENV=test`. Use a dedicated PostgreSQL role with only `LOGIN` and `CREATEDB`;
`.env.test.example` shows how an administrator creates it. A superuser is refused unless
`.env.test` itself contains `TEST_DB_ALLOW_SUPERUSER=1` (not recommended; the shell cannot
set it).

Frontend (Node 26, see `frontend/.nvmrc`):

```bash
cd frontend
npm ci
```

## Running the backend tests

```bash
python -m pytest
```

Supported from the repository root and from `tests/`. Every run creates its own disposable
database (`medibridge_test_<random>`), empties it before and after each test, and drops it at
the end of a normal run. A run that is interrupted (for example, the process is killed) can
leave its database behind; later runs list such databases in a warning but never delete them
automatically. Existing databases are never used. Tests are collected only from `tests/`.

## API contract and architecture tests

Two test modules protect the structure of the API:

- `tests/test_api_contract.py` holds an explicit table of all 29 operations (method, path,
  handler name, operation ID, access rule, response model, status code, tags) and checks
  the application against it: the registered routes and the four FastAPI documentation
  routes, the OpenAPI document, routing through real requests, `401` for every protected
  operation without a token, and the allowed or denied outcome for each of the four
  roles. Changing a route, its access rule or its documented responses therefore
  requires a deliberate change to this table.
- `tests/test_router_architecture.py` checks that no module in `routers/` imports `main`
  or another router (absolute and relative imports), that each router is a plain
  `APIRouter` without its own prefix, tags or dependencies, that every router is
  registered, that no operation is registered twice, and that the application lifespan
  does no startup work: importing `main` in a fresh interpreter creates no engine, opens no
  connection and loads no migration, seeding or provisioning module, and running the
  lifespan builds no engine and opens no connection.
  `tests/test_app_lifespan.py` checks the shutdown side: an existing engine is disposed
  once (its real idle connections close), repeated lifespans each dispose only what
  exists, and a dispose failure is reported, not swallowed.

**FastAPI compatibility.** These tests list routes with `fastapi.routing.iter_route_contexts`,
because FastAPI includes routers lazily, and read some route attributes such as
`route.dependant`. These are framework details that a FastAPI upgrade can change; if the
tests fail after an upgrade, check them before suspecting the application. The
behavioural checks (real requests, the role matrix, OpenAPI) do not depend on them.

## PostgreSQL integration tests

The `tests/test_pg_*.py` modules check behaviour that depends on PostgreSQL itself:
constraints, transactions, persisted state and concurrency. They use the same disposable
database as every other test (created per run, emptied before and after each test, dropped
at the end of a normal run); there is no separate database lifecycle.

The shared SQLAlchemy engine (`db_engine.py`) also connects to that database, because
`DB_NAME` names it for the whole run. `DisposableDatabase.drop()` closes the engine's
idle pooled connections before it drops the session database, so ending them does not
rely on `WITH (FORCE)`; `tests/test_disposable_database.py` checks this on a scratch
database standing in for the session database (the pooled connection is gone while the
database still exists). If closing the pool fails, the identity-checked drop still runs and
the failure is reported afterwards. `tests/test_db_engine.py` checks the engine's settings, pool limits,
reuse, the wait for a free connection, rollback, hidden statement parameters, replacement
of server-closed connections and disposal, on separate small engines verified to be
connected to this run's database, and the transaction timeouts: that each is the
stricter of the limit and the session's own value (disabled, stricter, less strict,
equal, subsecond and minute values, each timeout independently), that they end with a
rolled-back or committed transaction, that they cancel a long statement and stop a lock
wait, that a failure to apply them propagates and leaves the pool usable, and that a
test's own `PGOPTIONS` session settings stay in force. Tests that hold a table lock bound
the holder itself with `idle_in_transaction_session_timeout`, so the server ends it and
releases the lock even if the engine's policy were removed or wrong, and they set a
session `statement_timeout` as a second bound.
`tests/test_provider_reads.py` compares every provider read with the pre-Phase 2 SQL run
through psycopg (rows, order, totals, keys, value types, adversarial search values),
checks the read-only transaction and connection release, a plain 500 when the pool is
exhausted (a one-connection pool with a 1 s wait; this is not a load test), the FHIR
resources and bundles, and provider writes alternating with pooled reads.
`tests/test_provider_writes.py` covers the provider writes with fixed expectations, run
against the previous `get_connection()` implementation before the change: the returned
dictionaries and persisted rows, `None` and `False` for missing providers, rejected writes
that name their constraint and persist nothing, the REST status codes and bodies, and
concurrent writers (a duplicate email waiting for the first writer's commit or rollback,
a delete waiting for a new appointment). It also checks what the move adds: one managed
read-write transaction per write with the engine's timeouts, rollback of the write when
its block fails, the SQLAlchemy-wrapped errors with a clean pooled connection afterwards,
and a plain 500 that changes nothing after the lock timeout.
`tests/test_appointment_reads.py` does the same for the appointment reads with
fixed expected results (order by start time then id, each filter and their combinations,
`0` and empty values as real filters, case-sensitive status, timezone-aware values), the
return contract (a plain list of plain dicts, or a plain dict or `None`, every row read
before the connection is released), the REST responses and validation, and appointment
writes through `get_connection()` before
pooled reads, including a rejected overlapping booking. The overlap rules themselves stay
in `tests/test_pg_appointments.py`. `tests/test_patient_reads.py` covers the patient
reads with fixed expected results, run against the previous implementation before the
change: the REST list and lookup, the FHIR Patient read and search (prefix and case
rules, `%`, `_` and backslash matched literally, identifier `system|value` and value-only
matches with each patient returned and counted once, paging, totals and links,
validation), the identifier reads, the permission checks, and REST and HL7 writes before
pooled reads. Its engine tests check which reads share a transaction: the search page and
total share one, and the identifiers are read in a separate one. The optional `DB_POOL_*`, `DB_CONNECT_TIMEOUT_SECONDS`,
`DB_STATEMENT_TIMEOUT_SECONDS` and `DB_LOCK_TIMEOUT_SECONDS` settings are handled like
the other connection settings: from `.env.test` when it exists, otherwise from the
environment. `tests/pg_helpers.py` holds the identity-checked connection and polling
helpers these modules share; `tests/test_pg_helpers.py` checks that its identity check
refuses a connection attached to any other database, and that a refused engine is
disposed.

| Module | What it checks | Tests |
|---|---|---|
| `test_pg_appointments.py` | Overlap rules from migration 005 through the real routes: identical, partial, containing and contained intervals, back-to-back, other providers, UTC offsets, cancelled and completed appointments, updates | 14 |
| `test_pg_concurrency.py` | Two transactions competing for the same appointment slot (A commits or rolls back), the same HL7 control ID, and the same MRN under different control IDs (service call, and real HTTP requests with JSON and HL7 acknowledgment responses) | 6 |
| `test_pg_patient_audit.py` | Patient handlers commit the change and its audit event together, write none for rejected changes, and roll both back on failure | 7 |
| `test_pg_providers.py` | Provider CRUD round trip; duplicate emails that differ only in case | 2 |
| `test_pg_fhir_practitioners.py` | Practitioner and PractitionerRole read, search, paging and 404 against real rows | 6 |
| `test_pg_auth.py` | Real users, sign-in, tokens, inactive users, roles, unique usernames and emails | 8 |
| `test_pg_audit_logs.py` | Audit-log order (including the id tie-breaker) and paging | 7 |
| `test_pg_schema.py` | Migration ledger and re-runs; constraints, foreign keys, indexes and `btree_gist` from the catalogs | 16 |
| `test_schema_drift.py` | `db_schema` metadata against the migrated schema: every table, column, default, identity, constraint, index and extension, built in a rolled-back scratch schema; changes to either side are detected (see [ADR 0002](decisions/0002-schema-metadata-and-drift-check.md)) | 43 |
| `test_alembic_baseline.py` | The Alembic baseline in fresh disposable databases against the `migrate.py` schema; refusals (existing, partial, wrong database, lock held, failing statement) change nothing; read-only migration-state inspection; `migrate.py` and Alembic exclude each other, and a drifted or mismatching result is never committed (see [ADR 0003](decisions/0003-alembic-baseline.md)) | 49 |

Run them as a group or one module at a time (from the repository root, same in bash and
PowerShell):

```bash
python -m pytest -k test_pg_
python -m pytest tests/test_pg_appointments.py
```

What is real and what is not:

- **Database.** Every test writes to and reads from the disposable database. Results are
  checked through a separate connection that is verified to be this run's database (name,
  OID, owner and marker, using the same checks as cleanup). IDs are always taken from
  responses or `RETURNING`, because cleanup does not reset sequences.
- **Authentication.** `test_pg_auth.py`, the HTTP wrong-scheme test in
  `test_auth_dependencies.py` and one audit-log test sign in for real. Other modules override
  the current user (with a real user row where audit events need one), because they test
  data behaviour, not authentication.
- **Injected failure.** `test_pg_patient_audit.py` has one deliberate fault: the handler's
  audit call runs the real insert on the handler's connection, then raises, so the test can
  check that both writes roll back. Nothing else is mocked in these modules.

**Concurrency.** Each scenario uses three connections: A holds an uncommitted write, B runs
the real service code in a worker thread, and an autocommit observer waits until
`pg_blocking_pids()` shows A blocking B before A commits or rolls back. The service module's
`get_connection` is wrapped for observation only (database identity, backend PID, timeouts);
it returns the real connection. `PGOPTIONS` gives every connection in these tests, B's
included, a 30 s lock timeout and a 45 s statement timeout; the observer waits at most 15 s,
and the worker is joined for at most 60 s. If the worker cannot be shown to have finished,
the run stops instead of continuing into cleanup. Blocking evidence is attached to each test
with `record_property`; to see it in a JUnit report, add
`-o junit_family=xunit1 --junitxml=report.xml`.

**Synthetic data only.** Use fictional names, `example.org` addresses and obviously
synthetic MRNs and message IDs.

**Local runs as a superuser.** If `.env.test` allows a superuser, the suite prints a warning;
CI runs these tests as the non-superuser `medibridge_tester` role.

**HL7 registrations with the same MRN.** `test_pg_concurrency.py` also covers two
`ADT^A04` messages for the same MRN with different control IDs, in the contention order
where both have passed the MRN lookup: the second is shown (through `pg_blocking_pids`) to
wait on the identifier insert, and after the first commits it reports `already_exists` for
the first patient, with one patient, one identifier and both messages recorded. The same
scenario also runs through `POST /hl7/adt` for the JSON response and the `AA`
acknowledgment. Registrations that do not overlap take the ordinary sequential
`already_exists` path instead. The tests assert that the waiting connection runs at
READ COMMITTED, which the recovery relies on.

## Coverage

Run coverage **from the repository root**. This is the command CI uses:

```bash
python -m pytest -q --tb=short -rs --cov --cov-report=term-missing:skip-covered --cov-report=json:coverage.json --cov-report=xml:coverage.xml
python scripts/coverage_summary.py coverage.json
```

The second command prints application line and branch coverage, safety-code coverage, the
enforced combined figure, and missing lines per file.

coverage.py reads its settings from `pyproject.toml` in the current directory, and its file
patterns are relative to that directory. **Coverage produced from `tests/` does not use the
project configuration (no branch coverage, wrong files, no threshold) and must not be used for
reporting or acceptance.**

**The threshold.** `fail_under = 85` applies to the combined figure:
(covered lines + covered branches) / (statements + branches), over the files listed in
`[tool.coverage.report] include` in `pyproject.toml`: the application modules (including
unused or poorly covered ones such as `patients.py`), `migrate.py`, and the test-database
safety code. Test cases and `scripts/` are not measured. The run fails below 85%. The baseline
when the threshold was introduced was 87.0%. The threshold is raised only by an explicit
change, after confirming the figure CI reports.

`tests/test_coverage_scope.py` fails if a file tracked by git is neither in that list nor a
deliberate exclusion, so new modules must be added to it.

## Linting and formatting (Python)

CI runs these commands, with the pinned Ruff from `requirements-dev.txt`, on a clean
checkout:

```bash
ruff check --output-format=github .
ruff format --check .
```

What they cover with the pinned Ruff (0.17.6):

- `ruff check` lints Python files and `pyproject.toml`.
- `ruff format --check` checks Python files **and Python code blocks inside Markdown
  files**, such as the examples in `docs/`. A documentation change can therefore fail the
  format check.

**Authoritative check before pushing:** run the two CI commands above, unchanged, in a
clean checkout of the commit you will push (for example a fresh `git clone`).

**Convenient check in a working copy.** In a working copy, `.` also picks up untracked files
(personal or scratch scripts) that CI never sees. To check only tracked files, pass them
explicitly:

```bash
# bash
ruff check $(git ls-files '*.py' '*.md' 'pyproject.toml')
ruff format --check $(git ls-files '*.py' '*.md' 'pyproject.toml')
```

```powershell
# PowerShell
ruff check (git ls-files '*.py' '*.md' 'pyproject.toml')
ruff format --check (git ls-files '*.py' '*.md' 'pyproject.toml')
```

These matched the CI commands' results for the files tracked at the time they were
written, but an explicit file list is not guaranteed to match Ruff's own discovery in every
case (for example after a Ruff upgrade or a configuration change). Treat them as a quick
check, not as a substitute for the clean-checkout run.

To apply formatting to tracked files: `ruff format $(git ls-files '*.py' '*.md')` (bash) or
`ruff format (git ls-files '*.py' '*.md')` (PowerShell).

## Frontend checks

From `frontend/`, the same commands as CI:

```bash
npm run lint
npx --no-install tsc -b
npm test
npm run build
```

`npm test` runs Vitest once and exits.

## Dependency audits

```bash
# Python: the installed environment, including transitive dependencies
pip-audit --strict --progress-spinner off

# npm (from frontend/)
npm audit --omit=dev --audit-level=moderate
npm audit --audit-level=high
```

What fails CI:

| Check | Fails on |
|---|---|
| `pip-audit --strict` | Any known vulnerability, any package that cannot be audited, or a service error |
| `npm audit --omit=dev --audit-level=moderate` | Moderate or higher in production dependencies |
| `npm audit --audit-level=high` | High or critical in any dependency, including development tooling |

Moderate findings that affect only development tooling are reported in the output but do not
fail CI on their own. Registry or network errors fail the npm checks. Exceptions require
maintainer approval and an expiry date.

## Test-database safety guard: what it does and does not cover

The repository-root `conftest.py` validates `.env.test` and owns the disposable database.
`tests/isolation_guard.py`, loaded for every run through `addopts` in `pyproject.toml`,
refuses any run in which that setup did not happen, before tests or application modules are
imported. It also disables loading of the development `.env`.

Covered: accidental invocations that skip the root `conftest.py`, such as `--noconftest` or
`--confcutdir`, as well as invalid or missing test configuration.

**libpq environment variables.** Every test connection passes host, port, user, password
and database explicitly, but libpq still reads some variables from the environment:
`PGHOSTADDR` sends the connection to another address while the host still reads as
`DB_HOST`, `PGSERVICE` loads settings (such as `hostaddr` or `options`) from a service file,
and `PGOPTIONS` sets session parameters such as `search_path` on every connection. A run
that inherits any of these three, even empty, stops before its first connection with an
error that names the variable. Tests that need one set it themselves (the concurrency and
engine tests set `PGOPTIONS` for their timeouts). Other `PG*` variables are accepted: the explicit
settings override `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` and `PGPASSWORD`; a password
file is not consulted when a password is given; `PGSERVICEFILE` and `PGSYSCONFDIR` only
matter together with `PGSERVICE`; transport, timing and display settings such as
`PGSSLMODE`, `PGCONNECT_TIMEOUT` or `PGTZ` do not change where a connection goes.

The harness's own connections (create, clean, drop) also check the address they actually
reached (libpq's `hostaddr`; the reported host and port only repeat the settings) before
running anything: a Unix-socket host must give a socket connection, an IP address must be
reached exactly, and `localhost` must give a loopback address. For any other host name
added to `TEST_DB_ALLOWED_HOSTS` only a TCP connection is required; the name is not
resolved, so where it leads is not verified. This check runs after the connection is
established: it keeps statements away from a wrong server, not the password. Preventing the
redirection is the job of the startup refusal. `tests/test_libpq_environment.py` covers
both.

Not covered: deliberately disabling the guard (`-p no:tests.isolation_guard`, overriding
`addopts`, using another configuration file), running test code outside pytest, or arbitrary
code. Do not use those options.

## CI

`.github/workflows/ci.yml` runs three jobs on pull requests to `main`, pushes to `main` and manual dispatch:
backend (Ruff, tests with coverage against PostgreSQL 18 as the non-superuser
`medibridge_tester`), frontend (lint, type-check, tests, build) and dependency audits. Branch
protection (making these checks required) is configured separately in repository settings;
it is currently not configured, so the checks are not enforced before merging.

## Latest reviewed measurements

Measured locally on Windows on 2026-11-21, with the PostgreSQL integration tests added to the
application code of revision `4e6ddc8`. This is a snapshot, not updated automatically; rerun
the commands above for current figures.

| Check | Result |
|---|---|
| Backend tests | 794 passed, 2 skipped (intentional), about 51 s |
| PostgreSQL integration tests (`-k test_pg_`) | 63 passed, about 19 s |
| Combined coverage | 92.66% (enforced minimum 85%) |
| Frontend tests | 176 passed |

Before the integration tests (revision `4e6ddc8`): 731 passed, 2 skipped, about 39 s, 90.97%.

GitHub Actions reports a pass or fail result for each CI job; the figures above are local
measurements, not taken from CI logs.
