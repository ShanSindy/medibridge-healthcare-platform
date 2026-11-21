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
  does no work.

**FastAPI compatibility.** These tests list routes with `fastapi.routing.iter_route_contexts`,
because FastAPI includes routers lazily, and read some route attributes such as
`route.dependant`. These are framework details that a FastAPI upgrade can change; if the
tests fail after an upgrade, check them before suspecting the application. The
behavioural checks (real requests, the role matrix, OpenAPI) do not depend on them.

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

CI runs, on a clean checkout:

```bash
ruff check .
ruff format --check .
```

In a working copy, `ruff check .` also inspects any untracked Python files present (personal
or scratch scripts), which CI never sees. To check exactly what CI checks, pass only tracked
files:

```bash
# bash
ruff check $(git ls-files '*.py')
ruff format --check $(git ls-files '*.py')
```

```powershell
# PowerShell
ruff check (git ls-files '*.py')
ruff format --check (git ls-files '*.py')
```

To apply formatting to tracked files: `ruff format $(git ls-files '*.py')` (bash) or
`ruff format (git ls-files '*.py')` (PowerShell).

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

Measured locally on Windows at revision `b995c38` on 2026-11-21. This is a snapshot, not
updated automatically; rerun the commands above for current figures.

| Check | Result |
|---|---|
| Backend tests | 731 passed, 2 skipped (intentional) |
| Combined coverage | 90.97% (enforced minimum 85%) |
| Frontend tests | 176 passed |

GitHub Actions reports a pass or fail result for each CI job; the figures above are local
measurements, not taken from CI logs.
