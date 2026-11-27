# Architecture

How the current MediBridge code is put together. It describes what exists today; planned
changes are listed in the [roadmap](../README.md#roadmap). The system-level diagram is in
the [README](../README.md#architecture); this page adds module responsibilities and the
request, HL7 and FHIR flows.

## Application composition

`main.py` creates the FastAPI application (`main:app`), registers one exception handler
(request-validation errors under `/fhir/` become FHIR `OperationOutcome` responses; all
other paths keep FastAPI's default) and includes eight routers. There is no middleware
and no CORS configuration. The lifespan does no startup work (no database connection,
migration or seeding); at shutdown it closes the shared database engine's idle
connections, if a request ever created the engine.

| Router module | Operations | Persistence path |
|---|---|---|
| [routers/system.py](../routers/system.py) | `GET /`, `GET /health` | None |
| [routers/auth.py](../routers/auth.py) | login, current user, admin check | `auth_service` → `user_service` |
| [routers/audit_logs.py](../routers/audit_logs.py) | `GET /audit-logs` | `audit_service` (shared SQLAlchemy engine) |
| [routers/patients.py](../routers/patients.py) | Patient CRUD | Reads: `patient_service` (shared SQLAlchemy engine). Writes: **inline SQL** on `get_connection()` in the handlers, audit rows via `audit.log_audit_event` on the same connection |
| [routers/providers.py](../routers/providers.py) | Provider CRUD | `provider_service` (reads on the shared SQLAlchemy engine, writes on `get_connection()`) |
| [routers/appointments.py](../routers/appointments.py) | Appointment create, read, list, update, cancel | `appointment_service` (reads on the shared SQLAlchemy engine, writes on `get_connection()`) |
| [routers/hl7.py](../routers/hl7.py) | `POST /hl7/adt` | `hl7_service` (with `hl7_tracking`) |
| [routers/fhir.py](../routers/fhir.py) | CapabilityStatement, Patient, Practitioner, PractitionerRole | `patient_service` reads for Patient, identifier reads in `database.py` (both on the shared SQLAlchemy engine); `provider_service` reads (shared SQLAlchemy engine) for Practitioner and PractitionerRole |

Routers are plain `APIRouter` objects without prefixes or shared dependencies. Each
route declares its own access rule, and no router imports `main` or another router.
Tests enforce these rules (see
[development-and-testing.md](development-and-testing.md#api-contract-and-architecture-tests)).

## Supporting modules

| Module | Responsibility |
|---|---|
| [auth_dependencies.py](../auth_dependencies.py) | `get_current_user` (bearer token → active user), `require_roles`, `require_permission` |
| [permissions.py](../permissions.py) | Permission → roles mapping |
| [token_service.py](../token_service.py) | Issues and verifies HS256 access tokens; reads `JWT_SECRET_KEY` at import |
| [security.py](../security.py) | Argon2id hashing and verification |
| [auth_service.py](../auth_service.py) | Username and password check, including a dummy hash check for unknown users |
| [user_service.py](../user_service.py) | Create and look up users |
| [provider_service.py](../provider_service.py), [appointment_service.py](../appointment_service.py) | SQL for providers and appointments |
| [patient_service.py](../patient_service.py) | Patient reads: list, lookup, and the FHIR search with its page and total |
| [audit.py](../audit.py), [audit_service.py](../audit_service.py) | Write audit events (optionally inside the caller's transaction); read them |
| [hl7_parser.py](../hl7_parser.py), [hl7_service.py](../hl7_service.py), [hl7_tracking.py](../hl7_tracking.py), [hl7_ack.py](../hl7_ack.py) | Parse and validate ADT messages, write patients, track message IDs, build ACKs |
| [fhir.py](../fhir.py) | Map database records to FHIR resources; build `OperationOutcome` |
| [database.py](../database.py) | `get_connection()` from the `DB_*` settings; patient identifier reads (shared engine) |
| [db_engine.py](../db_engine.py) | The shared SQLAlchemy engine: validated `DB_*` settings, one bounded connection pool per process |
| [db_errors.py](../db_errors.py) | Map named constraint violations to 404 or 409 responses, per endpoint |
| [models.py](../models.py) | Pydantic request and response models |
| [migrate.py](../migrate.py), [migrations/](../migrations) | Plain SQL schema migrations, applied in order and recorded in `schema_migrations` |
| [db_schema.py](../db_schema.py) | The schema as SQLAlchemy Core metadata, descriptive only and checked against the migrations ([ADR 0002](decisions/0002-schema-metadata-and-drift-check.md)); not used at runtime |
| [patients.py](../patients.py) | Legacy static list of synthetic patients; not used by the application |

## Data access

- **Connections.** Database access is moving to SQLAlchemy Core one service at a time
  (see [ADR 0001](decisions/0001-shared-sqlalchemy-engine.md)); both paths exist today.
  - Most operations open their own connection with `database.get_connection()` (psycopg 3,
    no pool, no connection timeout). Connection blocks commit when they finish normally
    and roll back when an exception escapes.
  - The audit-log read (`audit_service`), the provider reads (`provider_service`:
    list, lookup and search, also behind FHIR Practitioner and PractitionerRole) and the
    appointment reads (`appointment_service`: list with its optional patient, provider
    and status filters, and lookup) and the patient reads (`patient_service`: list,
    lookup and the FHIR Patient search, behind the REST patient routes and FHIR Patient;
    and the identifier reads in `database.py`) use the shared engine in `db_engine.py`: a bounded
    pool (by default 5 connections plus 5 overflow, a 10 s wait for a free connection, a
    10 s connect timeout), each connection tested before use. They read through
    `db_engine.read_only_connection()`: one transaction per call that PostgreSQL keeps
    read-only, never committed, rolled back when the connection returns to the pool. Provider writes (create, update, delete)
    and appointment writes (create, update, cancel) still use `get_connection()`, so the
    overlap rule from migration 005 and its 409 handling are unchanged. So do patient
    writes (REST create, update, delete, each with its audit event on the same
    connection) and HL7 registration and update, including the MRN lookup that runs
    inside the HL7 registration transaction. Writes on the
    engine, when they move, will use `begin()` blocks, which commit on success and roll
    back on error.
  - Every engine transaction starts with a transaction-local `statement_timeout` and
    `lock_timeout`: the configured limit (30 s and 5 s by default), or the session's own
    value where that is stricter (0, "disabled", never counts as stricter). They end
    with the transaction. They bound each statement's time in PostgreSQL and each wait
    for a lock; there is no deadline for a whole request or transaction of several
    statements, and they do not bound the wait for a free connection (pool timeout),
    the connect timeout, or a network stall. `get_connection()` connections, and so all
    writes (appointment writes included), do not inherit this application-configured,
    transaction-local policy; existing PostgreSQL server or session timeout settings
    may still apply to them.
  - Provider search runs its page and its total in one READ COMMITTED transaction, as
    before: each statement sees the data committed when it starts, so a provider added
    or removed in between can make the total disagree with the page.
  - The engine belongs to the process. It is created on first use, never at startup,
    and disposed at application shutdown; connections still checked out at that moment
    are not interrupted (see the ADR for the exact contract).
  - The pool limit applies per process and only to the engine. The engine can hold at
    most the number of worker processes × (pool size + overflow) connections; every
    connection opened through `get_connection()` comes on top, without a limit.
- **SQL.** All values are passed as query parameters. The search and filter queries
  (FHIR Patient search, appointment list) assemble their `WHERE` clause from fixed
  fragments in the code; provider search builds it with SQLAlchemy Core expressions and
  bound parameters. User input never becomes SQL text.
- **Constraints.** The schema enforces foreign keys, uniqueness (provider email,
  username, user email, patient identifier, HL7 message ID), value checks, and an
  exclusion constraint against overlapping scheduled appointments for a provider
  (PostgreSQL `btree_gist`). The patient, provider and appointment routers translate the
  relevant violations into 404 or 409 responses through `db_errors`; unexpected
  violations remain server errors.
- **Audit.** Only REST patient create, update and delete write audit events, in the same
  transaction as the change. Provider, appointment and HL7 writes, reads, and logins are
  not audited ([security.md](security.md#known-limitations)).

## Authenticated request flow

Example: `POST /patients`. FastAPI resolves the route's dependencies before it validates
the request body, so authentication and authorization failures end the request before
the body is examined.

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant R as routers/patients.py
    participant A as auth_dependencies
    participant DB as PostgreSQL

    C->>R: POST /patients, bearer token, JSON body
    R->>A: resolve dependencies
    A->>A: verify token signature, expiry and required claims
    alt token missing, invalid or expired
        A-->>C: 401 Invalid or missing authentication credentials
    else token valid
        A->>DB: look up user by token subject
        alt user not found or inactive
            A-->>C: 401 Invalid or missing authentication credentials
        else role lacks patients:write
            A-->>C: 403 Insufficient permissions
        else authorized
            R->>R: validate body against PatientCreate
            alt body invalid
                R-->>C: 422 validation errors
            else body valid
                R->>DB: one transaction: INSERT patient, INSERT audit event
                alt both statements succeed
                    DB-->>R: commit
                    R-->>C: 201 Patient
                else a statement fails
                    DB-->>R: rollback, neither row is kept
                    R-->>C: 500 Internal Server Error
                end
            end
        end
    end
```

Public routes (`/`, `/health`, `/fhir/metadata`, `/auth/login`) have no authentication
dependency. `/auth/me` needs only a valid token; `/auth/admin-check` checks the `admin`
role directly. All other routes check a named permission.

## HL7 ADT processing

```mermaid
flowchart TD
    req["POST /hl7/adt"] --> auth{"Valid token and<br/>patients:write?"}
    auth -- "no" --> e401["401 or 403, JSON"]
    auth -- "yes" --> ct{"Content-Type text/plain?"}
    ct -- "no" --> e415["415, JSON"]
    ct -- "yes" --> parse{"Structure valid?<br/>one MSH and one PID,<br/>type ADT^A04 or ADT^A08,<br/>control ID present"}
    parse -- "no" --> e400["400, JSON<br/>(no ACK: the message could not be parsed)"]
    parse -- "yes" --> fields{"PID fields and<br/>sender MSH-3 and MSH-4 valid?"}
    fields -- "no" --> ar["400: JSON, or ACK AR if requested<br/>nothing written"]
    fields -- "yes" --> track["Open one transaction<br/>record sender + control ID in hl7_messages"]
    track -- "already recorded" --> dup["status duplicate<br/>nothing new written"]
    track -- "new ADT^A04" --> mrn{"MRN already registered?"}
    mrn -- "yes" --> exists["status already_exists<br/>message recorded, patient unchanged"]
    mrn -- "no" --> created["insert patient and MRN identifier<br/>commit, status created"]
    track -- "new ADT^A08" --> found{"patient with MRN?"}
    found -- "yes" --> updated["update patient<br/>commit, status updated"]
    found -- "no" --> notfound["rollback, message not recorded<br/>400: JSON, or ACK AR if requested"]
    track -- "unexpected error" --> ae["rollback<br/>500, or ACK AE if requested"]
    dup --> ok["200: JSON result,<br/>or ACK AA if requested"]
    exists --> ok
    created --> ok
    updated --> ok
    ok -.- audit["No audit event is written for HL7 processing"]
```

"If requested" means the request's `Accept` header contains `application/hl7-v2`. The
version field MSH-12 is not checked. Field mappings, ACK format and limitations are in
[interoperability.md](interoperability.md#hl7-v2-adt-intake).

## FHIR request handling

```mermaid
flowchart TD
    meta["GET /fhir/metadata<br/>public"] --> cs["CapabilityStatement<br/>application/json"]

    rq["GET /fhir/Patient, /fhir/Practitioner, /fhir/PractitionerRole<br/>and /{id} reads"] --> auth{"Valid token and permission?<br/>Patient: patients:read<br/>Practitioner, PractitionerRole: providers:read"}
    auth -- "no" --> plain["401 or 403<br/>plain JSON, not OperationOutcome"]
    auth -- "yes" --> params{"Valid parameters?<br/>numeric id, _count 1 to 100, _offset 0 or more"}
    params -- "no" --> oo422["422 OperationOutcome, code invalid<br/>application/fhir+json"]

    params -- "Patient" --> psql["patient_service<br/>identifiers from database.py"]
    params -- "Practitioner, PractitionerRole" --> svc["provider_service"]
    psql --> pmap["fhir.patient_to_fhir"]
    svc --> rmap["fhir.provider_to_fhir<br/>fhir.provider_to_practitioner_role"]

    pmap --> pout{"read or search"}
    rmap --> rout{"read or search"}
    pout -- "read, not found" --> oo404["404 OperationOutcome, code not-found<br/>application/fhir+json"]
    rout -- "read, not found" --> oo404
    pout -- "read" --> pres["Patient<br/>application/json"]
    pout -- "search" --> pbundle["searchset Bundle<br/>application/json"]
    rout -- "read" --> rres["Practitioner or PractitionerRole<br/>application/fhir+json"]
    rout -- "search" --> rbundle["searchset Bundle<br/>application/fhir+json"]
    pbundle -.- links["Bundle: total, absolute entry fullUrl,<br/>relative self, next and previous links"]
    rbundle -.- links
```

## Frontend

The React dashboard in [frontend/](../frontend) is a separate Vite application. Pages
call only `src/services/`, which return either bundled synthetic data (demo mode) or
backend responses (API mode, Dashboard, Patients and Providers only). In development the
Vite server proxies `/api/*` to the backend because the backend has no CORS
configuration. Details: [frontend/README.md](../frontend/README.md).
