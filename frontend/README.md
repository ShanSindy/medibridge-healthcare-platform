# MediBridge Healthcare Platform — Dashboard

React dashboard for the MediBridge Healthcare Platform. It runs on synthetic
demo data by default, and the **Dashboard** and **Patients** pages can
optionally read live data from the MediBridge FastAPI backend.

**Stack:** React 19 · TypeScript · Vite 8 · Tailwind CSS v4 · Vitest

> All demo records are synthetic. No real patient information is included in
> this repository.

## Quick start

Requires Node.js 22 or later.

```bash
cd frontend
npm install
npm run dev
```

Open the URL Vite prints (usually http://localhost:5173). The app starts in
**demo mode**, so no backend is needed.

| Command | Purpose |
|---|---|
| `npm run dev` | Start the development server with hot reload |
| `npm run build` | Type-check and build for production into `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm run lint` | Run ESLint |
| `npm test` | Run the automated tests once |
| `npm run test:watch` | Run tests in watch mode |

## Data modes

The data source is chosen at build/start time with `VITE_DATA_SOURCE`.

| Mode | Value | Dashboard and Patients | Everything else |
|---|---|---|---|
| Demo (default) | `demo` or unset | Synthetic records | Synthetic records |
| API | `api` | Live `GET /patients` and `GET /health` (sign-in required for patients) | Synthetic records |

Any value other than `api` falls back to demo mode. The top bar always shows
which mode is active.

### Demo mode

Nothing to configure — just run `npm run dev`. Under **Settings → Demo
environment** you can add simulated latency or force request failures to
review loading, error and retry states. These settings are stored in your
browser only.

### API mode

1. Start the FastAPI backend on port 8000 (see [Running the backend](#running-the-backend)).
2. Create `frontend/.env.local` (git-ignored). See `.env.example` for all options:

   ```bash
   VITE_DATA_SOURCE=api
   VITE_API_BASE_URL=/api
   ```

3. Restart `npm run dev`. Environment variables and `vite.config.ts` are only
   read when the server starts.
4. Open **Patients** and sign in with a MediBridge account. The Dashboard
   then shows live patient totals as well.

#### Running the backend

From the repository root, with PostgreSQL running:

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python migrate.py                # creates or updates the tables
uvicorn main:app --port 8000
```

The backend reads its settings from a `.env` file in the repository root:
`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` and `JWT_SECRET_KEY`
(at least 48 characters). `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` is optional and
defaults to 30. Keep this file out of version control.

There is no sign-up endpoint. Create an account with the backend's
`user_service.create_user(username, email, password, role)` function, using
the role `admin`, `clinician` or `scheduler` to be able to view patients.

#### Configuration reference

| Variable | Default | Exposed to browser | Description |
|---|---|---|---|
| `VITE_DATA_SOURCE` | `demo` | Yes | `demo` or `api` |
| `VITE_API_BASE_URL` | `http://localhost:8000` | Yes | Base URL for API requests. Use `/api` to go through the Vite proxy |
| `API_PROXY_TARGET` | `http://localhost:8000` | No | Where the `/api` proxy forwards requests |

Never put passwords, tokens or other secrets in `VITE_*` variables: they are
compiled into the JavaScript bundle.

#### Vite proxy and CORS

The backend does not currently enable CORS, so a browser on
`http://localhost:5173` cannot call `http://localhost:8000` directly (the
preflight request is rejected). The dev server and `npm run preview` therefore
proxy `/api/*` to the backend:

```
browser → http://localhost:5173/api/patients → http://localhost:8000/patients
```

Set `VITE_API_BASE_URL=/api` to use it, and `API_PROXY_TARGET` if the backend
is not on port 8000. The proxy exists only in Vite's dev and preview servers.
A production deployment needs either a reverse proxy that serves the dashboard
and API from the same origin, or CORS enabled on the backend for the
dashboard's origin.

## Authentication

API mode uses the backend's existing security model. Nothing is bypassed and
no credentials are stored in the code.

- **Sign-in:** the sign-in form on the Patients page posts username and password to
  `POST /auth/login` and receives a short-lived JWT. `GET /auth/me` is then
  used to show the user's name and role.
- **Requests:** the token is sent as `Authorization: Bearer …`. Cookies are not used.
- **Authorization:** enforced by the backend. `GET /patients` requires the
  `patients:read` permission (roles `admin`, `clinician` or `scheduler`).
  Other roles see an "access denied" message.
- **Token storage:** in memory only, never in localStorage or sessionStorage.
  Reloading the page or signing out discards it.
- **Expiry:** the session ends automatically when the token expires, even if
  the page is idle, and any 401 response also ends it. Patient data is then
  removed from the screen and the user is asked to sign in again. The backend
  has no refresh tokens, so re-authentication is expected.

## What uses real data

| Area | Demo mode | API mode |
|---|---|---|
| Dashboard | Synthetic: summary cards, charts, schedule, activity | **Live API**: patient total and demographics, API health. Other sections are hidden |
| Patients | Synthetic | **Live API**: name, date of birth, gender |
| Providers, Appointments | Synthetic | Synthetic |
| FHIR Explorer, HL7 Integration, Audit Logs | Informational placeholders | Informational placeholders |
| Settings | Saved in the browser only | Saved in the browser only |

A failed API request is always shown as an error with a retry button. Demo
records are never substituted for live data.

The backend patient endpoint returns only `id`, `first_name`, `last_name`,
`date_of_birth` and `gender`. In API mode the dashboard does **not** invent
the other fields shown in demo mode (MRN, contact details, care status,
primary provider, last visit, registration source). Those columns and the
status filter are hidden, and a notice explains why.

API responses are validated before display. A response that is not a list, or
that contains a malformed record (for example an impossible date of birth such
as `1990-02-30`), is rejected as a whole and shown as an error rather than
partially displayed.

## Dashboard

In demo mode the Dashboard shows:

- **Summary cards** for patients, providers, upcoming appointments and HL7
  message volume, each linking to its page.
- **Charts** for appointment activity (14 days, by status), patient
  registrations (12 months, by source), HL7 message processing (24 hours,
  accepted and rejected) and patient demographics (gender or age group).
  Hover a bar for exact values; the same numbers are available to screen
  readers as tables.
- **Today's schedule** and **recent activity**.
- **System status** and a **Refresh** button with the time of the last
  successful refresh.

The charts are built from plain HTML and CSS, with no chart library. Every
figure is computed from the synthetic records in `src/data/demo/`.

In API mode the Dashboard shows only what the backend can actually answer:
the patient total and demographics from `GET /patients`, and whether the API
responds to `GET /health`. The backend has no summary, trend or activity
endpoints, so those sections are hidden rather than filled with demo data.
The health endpoint does not report on the database or the HL7/FHIR
endpoints, so System status marks them "Not reported" instead of healthy.

## Tests

```bash
npm test
```

Vitest runs in Node (no browser required). Tests live next to the code they
cover (`src/**/*.test.ts`) and stub `fetch`, so they never call a real server:

- `config/api.test.ts` — demo/API mode selection and base URL handling
- `services/http.test.ts` — request building and HTTP/network/timeout error mapping
- `services/auth.test.ts` — sign-in, invalid responses, expiry and sign-out
- `services/patientMapper.test.ts` — validation and snake_case → camelCase mapping
- `services/patients.test.ts` — demo vs API retrieval, 401/403, retries, invalid data
- `services/dashboardTrends.test.ts` — chart aggregations (months, days, hours, demographics)
- `services/systemStatus.test.ts` — health reporting, including unreachable and unhealthy APIs

The backend has its own test suite. From the repository root, with a
`.env.test` file pointing at a `medibridge_test` database, run
`python -m pytest -q`.

`npm test` runs through `scripts/vitest.mjs`, which normalises the Windows
drive-letter case of the working directory. Without it, Vitest fails every
suite with "failed to find the current suite" when started from a terminal
that reports `c:\` instead of `C:\`.

## Project structure

```
src/
  components/   layout (sidebar, top bar), ui primitives, charts, dashboard
                cards, auth, domain badges
  config/       data source and API base URL
  data/demo/    synthetic demo records
  hooks/        async data loading, hash routing, auth session
  lib/          formatting, dates, class-name helpers
  pages/        one component per route
  services/     data access: demo data, HTTP client, auth, patient mapping,
                dashboard aggregations, system status
  types/        domain models
```

Pages talk only to `src/services/`. Swapping a page from demo data to the API
does not change its components.

## Troubleshooting

**Styles missing, icons or the logo enormous.** The page is loading without
Tailwind's utility classes. If `npm run build` produces a normal-looking app,
the source is fine and the running dev server is stale. Stop it (Ctrl+C) and
start it again with `npm run dev`. This can happen after dependencies or
`vite.config.ts` change while the server is running.

**Blank page, with a console error such as "does not provide an export
named …".** Same cause: the dev server cached a file while it was being
rewritten, for example during a branch switch. Restart it.

**"Could not reach the MediBridge API".** Check that the backend is running,
that `VITE_API_BASE_URL=/api` is set, and that `API_PROXY_TARGET` points at
the backend.

**API requests go to the wrong place on Windows / Git Bash.** Git Bash
rewrites inline values such as `VITE_API_BASE_URL=/api npm run dev` into a
Windows path (`C:/Program Files/Git/api`). The app rejects such values and
falls back to `http://localhost:8000` with a console warning. Put the setting
in `.env.local` instead, or prefix the command with `MSYS_NO_PATHCONV=1`.

**Signed out unexpectedly.** The token expired or the backend rejected it.
Sign in again.

## Known limitations

- Only the Dashboard and Patients pages use the API. Providers, Appointments,
  FHIR Explorer, HL7 Integration and Audit Logs do not call the backend yet.
- `GET /patients` has no pagination or server-side search. The page loads the
  full list and filters it in the browser, which suits small data sets only.
- The API returns five patient fields. MRN, contact details, care status,
  primary provider, visit history and registration date are demo-only.
- There are no refresh tokens. Users sign in again after the token expires or
  the page reloads.
- The `/api` proxy exists only in Vite's dev and preview servers. Production
  needs a reverse proxy or CORS on the backend.
- Automated frontend tests cover the service layer. There are no component
  or browser tests in the repository.
