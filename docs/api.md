# API guide

How to authenticate and call the MediBridge API, with synthetic examples. The complete,
generated reference (every field and schema) is served by the running backend at
`/docs` (Swagger UI), `/redoc` and `/openapi.json`; this guide does not repeat it.

All examples use fictional data. Never send real patient information.

## Conventions

- Base URL in the examples: `http://localhost:8000`.
- Request and response bodies are JSON unless stated otherwise. Login uses an HTML form
  body; HL7 messages are plain text; FHIR endpoints are covered in
  [interoperability.md](interoperability.md).
- Errors use FastAPI's format, `{"detail": ...}`. Request validation failures return
  `422` with a list of problems in `detail`. FHIR endpoints return 404 and 422 errors as
  FHIR `OperationOutcome` resources instead.
- Lists (`GET /patients`, `GET /providers`, `GET /appointments`) return every matching
  record; there is no pagination.

## Operations and access

Public operations need no token. All others need `Authorization: Bearer <token>`. Missing,
invalid or expired tokens, and tokens of deactivated accounts, get `401` with
`WWW-Authenticate: Bearer`. A valid token whose role lacks the permission gets
`403 {"detail": "Insufficient permissions"}`. Authentication and authorization are
checked before the request body is validated.

| Method | Path | Access | Success |
|---|---|---|---|
| GET | `/` | Public | 200 |
| GET | `/health` | Public | 200 |
| POST | `/auth/login` | Public | 200 |
| GET | `/auth/me` | Any signed-in user | 200 |
| GET | `/auth/admin-check` | admin | 200 |
| GET | `/audit-logs` | `audit:read` (admin) | 200 |
| GET | `/patients`, `/patients/{patient_id}` | `patients:read` (admin, clinician, scheduler) | 200 |
| POST | `/patients` | `patients:write` (admin, clinician) | 201 |
| PUT | `/patients/{patient_id}` | `patients:write` | 200 |
| DELETE | `/patients/{patient_id}` | `patients:write` | 204 |
| GET | `/providers`, `/providers/{provider_id}` | `providers:read` (all four roles) | 200 |
| POST | `/providers` | `providers:write` (admin) | 201 |
| PUT | `/providers/{provider_id}` | `providers:write` | 200 |
| DELETE | `/providers/{provider_id}` | `providers:write` | 204 |
| GET | `/appointments`, `/appointments/{appointment_id}` | `appointments:read` (admin, clinician, scheduler) | 200 |
| POST | `/appointments` | `appointments:write` (admin, scheduler) | 201 |
| PUT | `/appointments/{appointment_id}` | `appointments:write` | 200 |
| PATCH | `/appointments/{appointment_id}/cancel` | `appointments:write` | 200 |
| POST | `/hl7/adt` | `patients:write` (admin, clinician) | 200 |
| GET | `/fhir/metadata` | Public | 200 |
| GET | `/fhir/Patient`, `/fhir/Patient/{patient_id}` | `patients:read` | 200 |
| GET | `/fhir/Practitioner`, `/fhir/Practitioner/{practitioner_id}` | `providers:read` | 200 |
| GET | `/fhir/PractitionerRole`, `/fhir/PractitionerRole/{role_id}` | `providers:read` | 200 |

That is 29 operations. Permissions are defined in [permissions.py](../permissions.py); the
`users:manage` permission is defined but not used by any route.

## Examples

The commands are written for a POSIX shell (bash, zsh, or Git Bash on Windows) with
`curl` and the project's Python environment active. In Windows PowerShell, use
Swagger UI or adapt them: call `curl.exe` rather than `curl` (an alias of
`Invoke-WebRequest` in Windows PowerShell 5.1) and adjust quoting.

```bash
API=http://localhost:8000
```

Response bodies below are examples. Identifiers and timestamps will differ, and `…`
marks content left out for brevity. Identifiers can have gaps: a rejected insert (for
example a conflict) still uses up a sequence value.

These examples were run in order, unchanged, in Git Bash on Windows against the real
application and a fresh PostgreSQL 18 database on 2026-11-21, together with the error
cases described below.

### Health

```bash
curl -s "$API/health"
```

```json
{"status": "healthy", "service": "MediBridge API"}
```

This shows that the process is running. It does not check the database.

### Authentication

`POST /auth/login` takes a **form-encoded** body (`application/x-www-form-urlencoded`)
with `username` and `password`, not JSON. The following prompts for the credentials,
sends the password through standard input (so it appears neither in the command line nor
in shell history), and keeps the token in a shell variable without printing it:

```bash
read -r -p "Username: " MB_USERNAME
read -r -s -p "Password: " MB_PASSWORD; echo
TOKEN=$(printf '%s' "$MB_PASSWORD" \
  | curl -s -X POST "$API/auth/login" \
      --data-urlencode "username=$MB_USERNAME" --data-urlencode "password@-" \
  | python -c "import json, sys; print(json.load(sys.stdin)['access_token'])")
unset MB_PASSWORD
AUTH="Authorization: Bearer $TOKEN"
```

A successful login returns:

```json
{"access_token": "…", "token_type": "bearer", "expires_in": 1800}
```

`expires_in` is in seconds (`JWT_ACCESS_TOKEN_EXPIRE_MINUTES` × 60). There are no refresh
tokens; sign in again when the token expires. Wrong credentials and inactive accounts
both return `401 {"detail": "Incorrect username or password"}`, and the snippet above
then fails with a Python `KeyError` instead of setting `TOKEN`. Missing form fields
return `422`.

Check the token:

```bash
curl -s -H "$AUTH" "$API/auth/me"
```

```json
{"id": 1, "username": "demo.admin", "email": "demo.admin@example.org", "role": "admin", "is_active": true}
```

### Patients

Create a patient (requires `patients:write`) and keep its `id`:

```bash
PATIENT_ID=$(curl -s -X POST "$API/patients" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"first_name": "Avery", "last_name": "Example", "date_of_birth": "1985-03-14", "gender": "female"}' \
  | python -c "import json, sys; print(json.load(sys.stdin)['id'])")
```

The response is `201`:

```json
{"id": 42, "first_name": "Avery", "last_name": "Example", "date_of_birth": "1985-03-14", "gender": "female"}
```

`first_name` and `last_name` are required (1–100 characters, not blank),
`date_of_birth` must not be in the future, and `gender` is one of `male`, `female`,
`other` or `unknown`; anything else returns `422`. The create is recorded in the audit
log in the same database transaction.

Read and list:

```bash
curl -s -H "$AUTH" "$API/patients/$PATIENT_ID"
curl -s -H "$AUTH" "$API/patients"
```

An unknown ID returns `404 {"detail": "Patient not found"}`. `PUT /patients/{patient_id}`
takes the same body as create; `DELETE` returns `204` with no body.

### Providers

Create a provider (admin only) and keep its `id`:

```bash
PROVIDER_ID=$(curl -s -X POST "$API/providers" -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"first_name": "Jordan", "last_name": "Sample", "specialty": "Cardiology", "email": "jordan.sample@example.org", "phone": "555-0100"}' \
  | python -c "import json, sys; print(json.load(sys.stdin)['id'])")
```

```json
{"first_name": "Jordan", "last_name": "Sample", "specialty": "Cardiology", "email": "jordan.sample@example.org", "phone": "555-0100", "id": 7, "created_at": "…"}
```

`email` and `phone` are optional; emails are stored in lower case and must be unique. A
duplicate email returns `409 {"detail": "A provider with this email already exists."}`.
`GET /providers` and `GET /providers/{provider_id}` are available to every role;
`PUT` and `DELETE` are admin-only.

### Appointments

Schedule an appointment for the patient and provider created above (admin or scheduler).
Timestamps must include a UTC offset, and `ends_at` must be after `starts_at`:

```bash
APPOINTMENT_ID=$(curl -s -X POST "$API/appointments" -H "$AUTH" -H "Content-Type: application/json" \
  -d "{\"patient_id\": $PATIENT_ID, \"provider_id\": $PROVIDER_ID, \"starts_at\": \"2031-01-06T09:00:00-06:00\", \"ends_at\": \"2031-01-06T09:30:00-06:00\", \"reason\": \"Synthetic follow-up visit\"}" \
  | python -c "import json, sys; print(json.load(sys.stdin)['id'])")
```

```json
{"patient_id": 42, "provider_id": 7, "starts_at": "…", "ends_at": "…", "reason": "Synthetic follow-up visit", "id": 3, "status": "scheduled", "created_at": "…"}
```

`starts_at` and `ends_at` come back as the same instants that were sent, expressed in the
database session's time zone, so the UTC offset may differ from the request.

| Case | Response |
|---|---|
| No UTC offset, or `ends_at` not after `starts_at` | `422` |
| Unknown `patient_id` or `provider_id` | `404 {"detail": "Patient not found"}` or `"Provider not found"` |
| Overlaps another **scheduled** appointment of the same provider | `409 {"detail": "This provider already has a scheduled appointment during the requested time."}` |

Back-to-back appointments (one ending exactly when the next starts) are allowed, and
cancelled or completed appointments do not block a time slot.

List with optional filters, then cancel:

```bash
curl -s -H "$AUTH" "$API/appointments?provider_id=$PROVIDER_ID&status=scheduled"
curl -s -X PATCH -H "$AUTH" "$API/appointments/$APPOINTMENT_ID/cancel"
```

Cancelling returns the appointment with `"status": "cancelled"`; an unknown ID returns
`404 {"detail": "Appointment not found"}`. `PUT /appointments/{appointment_id}` replaces
`starts_at`, `ends_at`, `status` (`scheduled`, `completed` or `cancelled`) and `reason`.

### Database integrity conflicts

Deleting a patient or provider who still has appointments is refused by the database:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE -H "$AUTH" "$API/patients/$PATIENT_ID"
```

returns `409` with `{"detail": "Patient has appointments and cannot be deleted."}`
(for providers: `"Provider has appointments and cannot be deleted."`). Cancelled
appointments still count; deleting a patient also deletes the patient's identifiers.

### Authentication and authorization failures

| Request | Response |
|---|---|
| No `Authorization` header, or an invalid or expired token | `401 {"detail": "Invalid or missing authentication credentials"}`, header `WWW-Authenticate: Bearer` |
| Valid token, insufficient role (for example a `viewer` calling `GET /patients`) | `403 {"detail": "Insufficient permissions"}` |

### HL7 ADT intake

`POST /hl7/adt` accepts one HL7 v2 message as `text/plain`. Save a synthetic message
such as the `ADT^A04` example in
[interoperability.md](interoperability.md#example-adta04-registration) as `adt-a04.hl7`, then:

```bash
curl -s -X POST "$API/hl7/adt" -H "$AUTH" -H "Content-Type: text/plain" --data-binary @adt-a04.hl7
```

```json
{"status": "created", "patient_id": 43, "mrn": "SYN-000123"}
```

Use `--data-binary`: `-d` would strip the line breaks between segments. To receive an HL7
acknowledgment instead of JSON, add `-H "Accept: application/hl7-v2"`. Other content
types return `415`. Statuses, ACK codes and duplicate handling are described in
[interoperability.md](interoperability.md#hl7-v2-adt-intake).

### FHIR

```bash
curl -s "$API/fhir/metadata"
curl -s -H "$AUTH" "$API/fhir/Patient?family=Exam&_count=10"
curl -s -H "$AUTH" "$API/fhir/Practitioner/$PROVIDER_ID"
```

Search parameters, paging, content types and error formats are described in
[interoperability.md](interoperability.md#fhir-r4).

### Audit log

```bash
curl -s -H "$AUTH" "$API/audit-logs?limit=20&offset=0"
```

Admin only. Returns `{"limit": 20, "offset": 0, "items": [...]}`, newest first. `limit`
is 1–100 (default 50). Only REST patient create, update and delete write audit events;
see [security.md](security.md#known-limitations).
