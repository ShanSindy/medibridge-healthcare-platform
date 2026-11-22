# Getting started

How to run MediBridge locally from a clean checkout: backend, database, first user and
frontend. MediBridge uses synthetic data only; never enter real patient information.

## How these instructions were verified

This guide was followed end to end on 2026-11-21 on Windows 11, from a fresh clone, with
Python 3.14, PostgreSQL 18.6 and Node.js 26, against a newly created database owned by a
new non-superuser role.

| Step | How it was verified |
|---|---|
| 2. Python environment (`py -3.14 -m venv`, `Activate.ps1`, `pip install`) | Executed in Windows PowerShell |
| 3. Role and database | The `CREATE ROLE` and `CREATE DATABASE ... OWNER` statements were executed with unique names through a script, which set the password the way `\password` does; the interactive `psql` session itself was not run |
| 4. `.env`: copy, placeholder rejected, key generated | Executed in Windows PowerShell; key command also in Git Bash |
| 5. Migrations, including `btree_gist`, as the non-superuser owner; re-run skips | Executed against real PostgreSQL |
| 6. `provision_user.py` | Tests run the command's code against real PostgreSQL (creation, duplicates, sign-in, target mismatch, lock timeout, rollback before commit) and the real entry point as a child process (help, refusal without a terminal). Console detection was checked with the real Windows API in a hidden console, with `NUL` and with a pipe. On 2026-11-22 the maintainer ran five manual console checks in Windows PowerShell 5.1 with Python 3.14.10 against an unreachable placeholder target (short password, mismatch, Ctrl+C, valid password with no database, piped input); all passed, and no password characters or masking characters appeared. Creating and signing in a user interactively against a live database was not done manually; database persistence and sign-in are covered by the automated tests |
| 8. Backend, `/health`, `/docs`, `/redoc`, `/openapi.json`, sign-in | Executed against real PostgreSQL |
| 9. Frontend `npm ci`, demo mode, API mode through the `/api` proxy, sign-in | Executed (Git Bash and headless Chrome on Windows) |
| POSIX commands | Run in Git Bash on Windows only; not executed on Linux or macOS |

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
CREATE ROLE medibridge_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
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

CI exercises exactly this arrangement (every migration runs on PostgreSQL 18 as a
non-superuser role that owns its database), and so did the verification run of this
guide.

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

## 6. Create the first user

There is no sign-up endpoint and no default account. Create users with
`provision_user.py`, from the repository root with the environment active:

```bash
python provision_user.py --database medibridge_dev --username jordan.admin --email jordan.admin@example.org --role admin --confirm-admin
```

What the command does, in order:

1. Checks the arguments. `--role` is one of `admin`, `clinician`, `scheduler` or `viewer`;
   an `admin` also requires `--confirm-admin`. The username must not contain whitespace or
   control characters (at most 50 characters). The email address gets a basic check of this
   command only, not an application-wide policy: exactly one `@`, a non-empty local part,
   and a domain of at least two labels (letters, digits, inner hyphens; internationalised
   domains in punycode).
2. Reads `DB_*` settings from the environment, then from `.env` next to the script for
   anything the environment does not set (the application's own precedence;
   `PYTHON_DOTENV_DISABLED` is honoured). `--database` must equal `DB_NAME`. It prints the
   target database, host, port and database role, never the database password.
3. Requires an interactive terminal, then asks for the password twice without echo. It is
   never a command-line argument and must be 12 to 1024 characters (a rule of this command
   only). Without a terminal, or if the terminal cannot hide input, the command refuses.
   Nothing has connected to the database yet.
4. Opens one connection with a 10 s connect timeout, a 10 s lock timeout and a 30 s
   statement timeout (for this session only). On that connection it checks the actual
   database (`current_database()`), role (`current_user`, `session_user`) and port against
   the requested target, then inserts the user with the application's user service
   (Argon2id) and commits, all in one transaction on the same connection.

The identity check catches accidental mis-targeting, for example a connection-pooler alias
that maps the name to another database. It is not an authentication of the server; that
requires TLS with certificate verification, which MediBridge does not configure.

An existing username or email is reported and left unchanged; the command never updates or
resets a user. The schema must exist: run `python migrate.py` first.

Create an `admin` first; every further account (for example a `clinician` for viewing
patients) is created the same way, because the API has no user-management endpoints.

| Exit code | Meaning |
|---|---|
| 0 | User created and committed |
| 1 | Unexpected error (only the PostgreSQL error code or error type is shown); not committed |
| 2 | Invalid arguments, or `admin` without `--confirm-admin` |
| 3 | Missing `DB_*` settings, `--database` does not match `DB_NAME`, or the connected database, role or port differs; not committed |
| 4 | Password not accepted: too short or long, confirmation differs, or no secure terminal |
| 5 | Database unreachable, busy beyond the timeouts, or not migrated; not committed |
| 6 | A user with this username or email already exists; not committed |
| 7 | **Outcome unknown**: the connection failed or the command was interrupted while committing |
| 130 | Interrupted during password entry (nothing was sent to the database) or before the commit (not committed) |

"Not committed" is certain in every case except exit code 7, because those failures happen
before the command sends `COMMIT`. With exit code 7 the user may or may not exist. Do not
run the command again straight away; first check, for example with
`psql -d medibridge_dev -c "SELECT id, username, role, created_at FROM users WHERE username = 'jordan.admin'"`
or by signing in. Run the command again only if the user does not exist.

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

Open the URL Vite prints (normally <http://localhost:5173>; if that port is busy, Vite
picks the next free one and prints it). The dashboard starts in **demo mode** with
bundled synthetic data and needs no backend.

To use the backend, create `frontend/.env.local` (git-ignored) with:

```bash
VITE_DATA_SOURCE=api
VITE_API_BASE_URL=/api
```

and restart `npm run dev`. The backend has no CORS configuration, so the browser reaches
it through the Vite `/api` proxy, which forwards to `http://localhost:8000` (set
`API_PROXY_TARGET` to change it). Open **Patients** and sign in with an `admin`,
`clinician` or `scheduler` account. Only Dashboard, Patients and Providers read from the
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
| `401 Incorrect username or password` | Wrong credentials, an inactive account, or a user created in a different database (check the target printed by `provision_user.py`) |
| The browser shows CORS errors | Use `VITE_API_BASE_URL=/api` so requests go through the Vite proxy |
| Frontend problems such as missing styles or a blank page | See the troubleshooting section of the [frontend README](../frontend/README.md#troubleshooting) |
