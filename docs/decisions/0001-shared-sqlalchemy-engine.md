# ADR 0001: A shared SQLAlchemy engine, adopted one service at a time

Status: proposed (Phase 2, first slice). Date: 2026-11-23.

## Context

Before this change every database operation called `database.get_connection()`, which
opens a new psycopg 3 connection with no pool and no timeout (29 call sites in routers,
services and `migrate.py`; 28 after this change). The connection's `with` block commits
when it ends normally and rolls back when an exception escapes. Several modules depend on
that exact contract:

- HL7 registration uses savepoints (`conn.transaction()`) and relies on READ COMMITTED.
- Patient writes pass their connection to `audit.log_audit_event` so that the change and
  its audit row commit together.
- `tests/test_pg_concurrency.py` wraps `get_connection` and expects every call to open a
  fresh connection that picks up the test's `PGOPTIONS` lock and statement timeouts.

Phase 2 aims at SQLAlchemy 2 Core with a small, bounded pool and explicit transactions,
without a rewrite in one step.

## Decision

1. Add `db_engine.py`: one SQLAlchemy engine per process, built on first use from the
   `DB_*` settings, with a `QueuePool` (default 5 + 5 overflow, 10 s wait), a 10 s connect
   timeout, a pre-use liveness check, and statement parameters hidden from SQLAlchemy's
   error messages and statement log (`hide_parameters`).
2. Move exactly one consumer, `audit_service.get_audit_logs`, to the engine. It is
   read-only, owns its connection and shares no transaction with other code. Its SQL is
   unchanged apart from named bind parameters. It runs in a transaction that PostgreSQL
   itself keeps read-only (`postgresql_readonly`).
3. Leave `database.get_connection()` and every other caller unchanged.
4. Lifecycle: the application's lifespan (`main.lifespan`) disposes the engine at
   shutdown; the test harness disposes it before it drops the session database.

## Engine ownership and lifecycle

The engine belongs to the process, held in `db_engine`, not to the FastAPI application
object or a request. Services call `get_engine()` directly; giving the application
ownership (for example `app.state`) would mean passing the application or request into
every service, which is the dependency-injection change this slice avoids.

| Event | Behaviour |
|---|---|
| Import, application startup | Nothing. No engine object, no connection, no migration, seeding or provisioning module loaded (asserted by `tests/test_router_architecture.py`: importing `main` in a fresh interpreter, and running the lifespan) |
| First `get_engine()` | Builds the engine under a lock. Building opens no connection, so the lock is never held while waiting for the database |
| Application shutdown | `main.lifespan` calls `dispose_engine()`: idle pooled connections are closed and the engine is forgotten. Never creates an engine; a no-op if none exists |
| Repeated lifespans in one process (tests) | Each shutdown disposes whatever engine exists then; the next use builds a new one. (`uvicorn --reload` starts a new process instead) |
| Test session end | `DisposableDatabase.drop()` disposes the engine before `DROP DATABASE ... WITH (FORCE)`, only for the session database |
| Error during dispose | Not swallowed. The lifespan reports `lifespan.shutdown.failed` and re-raises. The test drop still runs its identity-checked drop (which ends any connection left open), then records and raises the error |

What `dispose_engine()` does not do (checked with SQLAlchemy 2.1.9):

- It does not interrupt a connection that is checked out. When that connection's block
  ends, the connection goes back to the discarded pool and is closed only when that pool
  is garbage-collected.
- Code that obtained the engine before the call and connects afterwards gets a connection
  from a new pool of the old engine, which nothing disposes.

So the supported contract is: dispose when no database work is in progress. Uvicorn runs
the lifespan shutdown after it stops accepting connections and has waited for open
requests. Two cases fall outside the contract, and their connections end when the process
exits:

- A forced exit (a second Ctrl+C) skips the lifespan shutdown, so nothing is disposed.
- If a graceful-shutdown timeout is set and expires, uvicorn cancels request tasks, but
  synchronous endpoints keep running in threadpool threads and may still hold, or later
  take, connections.

No extra locking is added for these cases: it would have to block shutdown on database
work.

## Transaction and connection ownership (engine path)

| Question | Answer |
|---|---|
| Who creates the connection | `get_engine().connect()` or `.begin()`, in the function that does the work |
| Who owns the transaction | The same `with` block. Nothing outside it commits or rolls back |
| Commit | Only `begin()` blocks, when they end without an exception. `connect()` never commits |
| Rollback | `begin()` on an exception; the pool on every check-in (`reset_on_return`), so no connection is handed out mid-transaction |
| Read-only reads | The audit read sets `postgresql_readonly`, so PostgreSQL refuses writes (SQLSTATE 25006). The pool resets the setting before the connection is reused |
| Release | End of the `with` block, normal or exceptional. A connection the server closed is detected before reuse and replaced |
| Retries | None |
| Pool exhausted | The caller waits up to `DB_POOL_TIMEOUT_SECONDS`, then gets `sqlalchemy.exc.TimeoutError` (a 500 response) |
| Threads | The pool is thread-safe; a connection is used by one thread at a time, inside one block |

## Configuration contract

- `DB_HOST`, `DB_PORT`, `DB_NAME` and `DB_USER` are required; blank counts as missing.
  The documentation and `.env.example` already listed them as required, and CI and the
  test harness set them. `get_connection()` instead passes a missing value to libpq as
  `None`, so libpq chooses its own default host, port, database or user (from `PG*`
  variables or built-in defaults). The engine refuses to start instead: a missing setting
  must not silently select another database. The explicit settings take precedence over
  `PGHOST`, `PGPORT`, `PGDATABASE` and `PGUSER` (tested for the first three). Other libpq
  variables still apply on both connection paths, for example `PGHOSTADDR` (which can
  send the connection to another address), `PGSERVICE`, `PGOPTIONS` and `PGSSLMODE`.
  The test harness refuses inherited `PGHOSTADDR`, `PGSERVICE` and `PGOPTIONS` and checks
  the address its own create, clean and drop connections reached. Outside tests the process
  environment is trusted; hardening the application's connections against these
  variables is a separate decision.
- `DB_PASSWORD` is optional, as before: absent or empty means libpq's own sources (a
  password file) or password-less authentication. `DB_HOST` may be a Unix-socket
  directory. Values are passed to the driver as separate arguments, never parsed as URL
  or connection-string syntax.
- `DB_PORT` must be one port number. A multi-host list such as `5432,5433`, which libpq
  accepts through `get_connection()`, is rejected; it was never documented.
- Pool settings: whole ASCII numbers only. Size 1–50, overflow 0–50, timeouts 1–300 s.
  The lower bounds exclude SQLAlchemy's "no limit" values (size 0, overflow -1). The upper
  bounds catch misconfiguration: 50 + 50 is PostgreSQL's whole default `max_connections`
  (100) for one process.
- Errors name the setting, never its value.

## Connection totals

The pool bounds only the engine's connections, and only per process. The most connections
MediBridge can hold is the number of worker processes × (`DB_POOL_SIZE` +
`DB_POOL_MAX_OVERFLOW`), plus every connection opened through `get_connection()`, which is
unbounded, plus administrative tools. There is no global limit of 10.

## Errors and sensitive data

- Database errors propagate as SQLAlchemy `DBAPIError` subclasses; the psycopg error is in
  `.orig`. Nothing on the audit-read path catches them, so the response is FastAPI's plain
  `500 Internal Server Error`, as before.
- `hide_parameters` removes bound parameter values from SQLAlchemy's error messages and
  its statement log. It does not hide the SQL text, or messages PostgreSQL writes itself:
  for example a unique-violation `DETAIL` names the duplicate key value, and an invalid
  input error quotes the input. The same is true on the psycopg path today.
- Connection errors do not include the password.
- Statement and lock timeouts were added in the second slice (see the amendment
  below). There is still no network read timeout and no TCP keepalive setting: a call on
  a connection whose server stops responding can still wait for the operating system's
  TCP timeout.

Before write paths move to the engine:

- `db_errors.raise_for_constraint` must read the constraint name from
  `IntegrityError.orig`, and handlers that catch psycopg errors must catch the SQLAlchemy
  wrappers.
- Unhandled errors must keep producing a plain 500, and server logs must be treated as
  possibly containing data from PostgreSQL's own messages.

## Alternatives considered

- **Engine only, no consumer.** Smallest diff, but it adds a dependency and code that
  nothing runs in production, so its behaviour would be unproven.
- **Pool behind `get_connection()`.** One change gives every service pooling, but it
  alters the contract above for all of them at once: session settings persist across
  uses, connect errors move to check-out, and the concurrency tests' per-connection
  timeouts stop applying. Too broad for one reviewable step.
- **A repository layer over psycopg first.** Adds an abstraction that the SQLAlchemy
  migration would replace.
- **`psycopg_pool`.** Matches the current contract closely, but services moving to
  SQLAlchemy Core would then need a second pool or a second migration.
- **Engine owned by the application object.** Rejected above (would need dependency
  injection through every service).

## Consequences

- Two connection paths exist until the remaining services move.
- New runtime dependency: SQLAlchemy 2.1.9 (its only requirement, `typing-extensions`, was
  already installed through Pydantic; no greenlet).
- The application now has shutdown work. The architecture test that asserted "no startup
  or shutdown work" now asserts no startup work, no engine or connection during the
  lifespan when none exists, and no migration, seeding or provisioning modules imported
  by `main`.

## Open questions for later slices

- The HL7 endpoint is `async` and calls the synchronous services directly, so database
  waits there block the event loop. That is true today; it matters more once those
  services wait on a bounded pool.

## Amendment: provider reads and transaction timeouts (Phase 2, second slice)

Status: proposed. Date: 2026-11-24.

- **Second consumer.** `provider_service` reads (`get_all_providers`,
  `get_provider_by_id`, `search_providers`, also used by FHIR Practitioner and
  PractitionerRole) moved to the engine. Their SQL is built with SQLAlchemy Core
  (`select()` on a lightweight `table()` of the selected columns; no ORM, no schema
  metadata), with every value bound. Results, order, totals, keys and value types are
  unchanged (tests compare them with the old SQL). Provider writes stay on
  `get_connection()`, so the router's psycopg constraint handling is untouched.
- **One read helper.** `db_engine.read_only_connection()` gives a pooled connection in a
  PostgreSQL read-only transaction. Both consumers use it; reads never commit.
- **Transaction timeouts.** Before more traffic moved onto the bounded pool, every engine
  transaction got a `statement_timeout` and a `lock_timeout`, each the stricter of a
  configured limit (`DB_STATEMENT_TIMEOUT_SECONDS`, default 30 s, 1-300;
  `DB_LOCK_TIMEOUT_SECONDS`, default 5 s, 1-60) and the session's current value. A
  session value of 0 means disabled and never counts as stricter. So the engine never
  weakens a stricter limit already in force (from PGOPTIONS, a role or database setting),
  and always applies its own where there is none.
  - An engine `begin` listener applies them in one statement before the transaction's
    first query. It reads the session's current values from `pg_settings.setting`, an
    integer in milliseconds for both settings (asserted by a test), so no unit strings
    such as `500ms` or `1min` are parsed, and sets the result with
    `set_config(..., true)`, the equivalent of `SET LOCAL`. The statement always returns
    exactly one row, so a missing setting cannot silently skip the policy. The values end with the transaction, committed or rolled
    back, and the session's own values apply again. The cost is one extra round trip per
    transaction.
  - The timeouts apply per statement and per lock wait. There is no deadline for a
    whole request: a transaction of several statements (search runs two) can take longer
    than one timeout, and the pool wait, connection setup and network stalls come on
    top. They do not apply under AUTOCOMMIT, which nothing uses: `set_config(..., true)`
    has no effect outside a transaction.
  - If applying them fails (for example on a broken connection), the error is raised
    where the transaction begins: by the first statement in a `connect()` block, or by
    `begin()` itself. That Connection must not be reused, because SQLAlchemy leaves it
    without a transaction: leave the block, as every caller does, and the pool rolls the
    connection back or replaces it.
  - 30 s and 5 s are starting values, not measured under load. The reads touch small
    tables (provider search is a full scan with prefix ILIKE), and a read waiting on a
    lock is normally waiting for DDL or a TRUNCATE, which should not take seconds.
  - Zero ("no limit") is refused. Nothing retries a cancelled statement; the error
    propagates (SQLSTATE 57014 for a statement timeout, 55P03 for a lock timeout,
    wrapped as `sqlalchemy.exc.OperationalError`) and the request fails with a plain 500.
  - `get_connection()` connections, and so every write, keep their current behaviour
    (no timeouts) until they move.
- **Unchanged isolation.** Provider search runs its page and count in one READ COMMITTED
  transaction, as before; they can disagree if providers change in between. A stronger
  isolation level was not introduced.
