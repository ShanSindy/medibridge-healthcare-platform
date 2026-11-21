# Getting started

How to run MediBridge locally from a clean checkout: backend, database, first user and
frontend. MediBridge uses synthetic data only; never enter real patient information.

## How these instructions were verified

| Step | Status |
|---|---|
| Python environment, dependency installation | Executed on Windows (Python 3.14) |
| `.env` placeholder rejected, key generation command | Executed in Windows PowerShell and Git Bash |
| Backend startup, `/`, `/health`, `/docs`, `/redoc`, `/openapi.json`, `/fhir/metadata`, 401 on protected routes | Executed with placeholder settings (no database) |
| Frontend `npm ci`, `npm run dev`, demo mode | Executed on Windows (Node.js 26) |
| Database and role creation, migrations as the database owner | Checked against the source and the CI workflow, which runs every migration on PostgreSQL 18 as a non-superuser database owner. Not executed by following this guide |
| First-user procedure, login, frontend API mode against a live database | Checked against the source. Not yet executed end-to-end |
| POSIX shell commands | Equivalent commands; not executed on Linux or macOS for this guide |

If a step fails for you, please report it.

## Prerequisites

| Software | Version | Notes |
|---|---|---|
| Python | **3.14** | Required: the code uses Python 3.14 syntax, so older versions fail with a `SyntaxError` |
| PostgreSQL | 18 is tested in CI | Must include the `btree_gist` extension (part of PostgreSQL's standard contrib modules) |
| Node.js | **26** | See [frontend/.nvmrc](../frontend/.nvmrc). Only needed for the dashboard |
| Git | Any recent version | |

## 1. Clone the repository

```bash
git clone https://github.com/ShanSindy/medibridge-healthcare-platform.git
cd medibridge-healthcare-platform
```

## 2. Create the Python environment

Windows PowerShell:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

POSIX shell (Linux, macOS, Git Bash):

```bash
python3.14 -m venv .venv
source .venv/bin/activate        # Git Bash on Windows: source .venv/Scripts/activate
python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` installs the runtime dependencies plus the test, lint, coverage
and audit tools. `requirements.txt` alone is enough to run the application. All later
commands assume the environment is active and the current directory is the repository
root.

## 3. Create the database and roles

MediBridge does not need a PostgreSQL superuser. A PostgreSQL administrator creates a
dedicated application role and a database owned by it, once:

```sql
-- Run as a PostgreSQL administrator, for example: psql -U postgres -d postgres
CREATE ROLE medibridge_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
\password medibridge_app
CREATE DATABASE medibridge_dev OWNER medibridge_app;
```

`\password` prompts for the password, so it does not appear in the command or in shell
history. The names `medibridge_app` and `medibridge_dev` are examples; choose names that
do not collide with existing databases.

Why ownership is enough:

- `python migrate.py` connects as `DB_USER` and creates the tables in the `public` schema.
  On PostgreSQL 15 and later, the database owner may create objects there.
- Migration 005 runs `CREATE EXTENSION IF NOT EXISTS btree_gist`. `btree_gist` is a
  trusted extension, so a role with `CREATE` privilege on the database (the owner has
  it) can install it, provided the extension files exist on the server. If PostgreSQL
  reports that the extension is not available, install your platform's PostgreSQL
  contrib package.

CI exercises exactly this arrangement: every migration runs on PostgreSQL 18 as a
non-superuser role that owns its database.

**Tests use a different role.** The test suite creates and drops its own disposable
databases, so it needs a separate role with `LOGIN` and `CREATEDB` and no access to your
development database. See [.env.test.example](../.env.test.example) and
[development-and-testing.md](development-and-testing.md). Do not reuse the application
role or the development database for tests.

## 4. Configure the backend

Copy the example file and edit the copy:

```powershell
Copy-Item .env.example .env        # PowerShell
```

```bash
cp .env.example .env               # POSIX shell
```

| Variable | Required | Description |
|---|---|---|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Yes | Connection to the database from step 3 |
| `JWT_SECRET_KEY` | Yes | Token signing key, **at least 48 characters**. The application refuses to start with the placeholder |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | No | Token lifetime, default 30 |

Generate a signing key and paste it into `.env` (the command prints the key, so do not
run it where others can see your screen):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

This produces a 64-character random key. `.env` is git-ignored; keep it that way.
Variables already set in your shell take precedence over `.env`.

## 5. Apply the migrations

```bash
python migrate.py
```

The script applies `migrations/000_*.sql` to `007_*.sql` in order, printing
`Applying <file>` for each new migration and `Skipping <file>` for those already
recorded in the `schema_migrations` table, so running it again is safe. Each migration
runs in its own transaction: if one fails, it is rolled back and not recorded, and the
script stops. It ends with `Database migrations complete.`

## 6. Create the first user (temporary developer procedure)

There is no sign-up endpoint, seed script or user-management command yet. Until a
supported provisioning command exists (planned before Phase 2), create users by calling
the existing `user_service.create_user` function from a Python session. This is a
developer onboarding step, not a supported production user-provisioning interface.

What the function does, from [user_service.py](../user_service.py):

- `create_user(username, email, password, role="viewer")` hashes the password with
  Argon2id and inserts one row into `users`, committing on success.
- It connects with the settings from `.env`, like the application.
- It has no password policy. The database enforces unique usernames and emails and a
  role of `admin`, `clinician`, `scheduler` or `viewer`; a violation raises an error and
  nothing is written.
- It returns the new user's `id`, `username`, `email`, `role`, `is_active` and
  `created_at` (never the password or hash).

Start `python` in the repository root and paste the first block. It only reads which
database and role you are connected to:

```python
from database import get_connection

with get_connection() as conn:
    print(conn.execute(
        "SELECT current_database(), current_user, inet_server_addr(), inet_server_port()"
    ).fetchone())
```

**Stop if that is not your development database.** Otherwise paste the second block in
the same session. It asks for each value. The password is read with `getpass`: it is not
echoed and does not appear in shell or Python history (the username, email and role you
type may be recorded in Python's history, so they should be synthetic too):

```python
from getpass import getpass
from user_service import create_user

user = create_user(
    username=input("Username: "),
    email=input("Email (use a reserved domain such as example.org): "),
    password=getpass("Password: "),
    role=input("Role (admin, clinician, scheduler or viewer): "),
)
print({key: user[key] for key in ("id", "username", "role")})
```

Create an `admin` account first. It can do everything else through the API.

## 7. Synthetic data

There is no seed or demo-data script for the backend. Create records through the API (see
[api.md](api.md)) or by sending synthetic HL7 `ADT^A04` messages (see
[interoperability.md](interoperability.md)). Use obviously fictional names, dates and
identifiers, and reserved domains such as `example.org` for email addresses.

## 8. Start the backend

```bash
uvicorn main:app --port 8000
```

| URL | Purpose |
|---|---|
| <http://localhost:8000/health> | Liveness: `{"status":"healthy","service":"MediBridge API"}` |
| <http://localhost:8000/docs> | Swagger UI |
| <http://localhost:8000/redoc> | ReDoc |
| <http://localhost:8000/openapi.json> | OpenAPI schema |

`/health` only shows that the application process is running. It does not check the
database; the first request that needs PostgreSQL does.

To check the database and your user together, sign in: in Swagger UI, open
`POST /auth/login`, choose **Try it out** and enter the username and password from step 6.
A `200` response with an `access_token` confirms both. Command-line examples are in
[api.md](api.md#authentication).

## 9. Run the frontend

```bash
cd frontend
npm ci
npm run dev
```

Open the URL Vite prints (normally <http://localhost:5173>). The dashboard starts in
**demo mode** with bundled synthetic data and needs no backend.

To use the backend, create `frontend/.env.local` (git-ignored) with:

```bash
VITE_DATA_SOURCE=api
VITE_API_BASE_URL=/api
```

and restart `npm run dev`. The backend has no CORS configuration, so the browser reaches
it through the Vite `/api` proxy, which forwards to `http://localhost:8000` (set
`API_PROXY_TARGET` to change it). Only Dashboard, Patients and Providers read from the
API; the [frontend README](../frontend/README.md) explains each mode.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `SyntaxError` when importing `security.py` | Python older than 3.14. Recreate `.venv` with Python 3.14 |
| `KeyError: 'JWT_SECRET_KEY'` | `.env` is missing or does not set it. Run commands from the repository root |
| `ValueError: JWT_SECRET_KEY must be at least 48 characters` | The placeholder or a short key is still in `.env`. Generate a key (step 4) |
| `migrate.py` fails at `005_prevent_appointment_overlaps.sql` | The role cannot create `btree_gist`, or the extension files are not installed. Check database ownership (step 3) and the PostgreSQL contrib package |
| `permission denied for schema public` during migrations | The role does not own the database. Recreate it with `OWNER medibridge_app` (step 3) |
| A request hangs instead of failing when PostgreSQL is down | Known limitation: connections are opened without a timeout. Check that PostgreSQL is running and the `DB_*` settings are correct, then stop the request |
| `/health` is fine but other requests fail | `/health` does not check the database. Check PostgreSQL and the `DB_*` settings |
| `.venv\Scripts\Activate.ps1` is blocked by execution policy | Run the environment's interpreter directly instead, for example `.venv\Scripts\python -m pip ...` and `.venv\Scripts\python migrate.py` |
| `401 Incorrect username or password` | Wrong credentials, an inactive account, or a user created in a different database (check step 6's first block) |
| The browser shows CORS errors | Use `VITE_API_BASE_URL=/api` so requests go through the Vite proxy |
| Frontend problems such as missing styles or a blank page | See the troubleshooting section of the [frontend README](../frontend/README.md#troubleshooting) |
