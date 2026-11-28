"""
The shared SQLAlchemy engine: one bounded PostgreSQL connection pool per process.

Ownership. The engine belongs to the process, not to a request or to the FastAPI
application object, because services reach it directly through `get_engine()`. It is
created on first use (never at import or application startup) from the `DB_*` settings,
and kept until `dispose_engine()`, which the application calls at shutdown (main.py) and
the test harness calls before it drops its database.

Code that uses it owns each connection through a `with` block:

    with read_only_connection() as conn:  # reads: PostgreSQL read-only, rolled back
        ...
    with write_transaction() as conn:     # writes: committed, or rolled back
        ...

When the block ends the connection goes back to the pool, which rolls back anything
still open before the connection is handed out again. Nothing is committed implicitly.

Timeouts. Every transaction on the engine starts with a transaction-local
`statement_timeout` and `lock_timeout`: the configured limit (`DB_STATEMENT_TIMEOUT_SECONDS`,
default 30; `DB_LOCK_TIMEOUT_SECONDS`, default 5), or the session's own value where that is
stricter (a positive value below the limit, for example from PGOPTIONS or a role setting;
0, "disabled", never counts as stricter). They end with the transaction, so nothing
carries over to the next user of a pooled connection, and the session's own values apply
again afterwards. Each bounds one statement's time in PostgreSQL or one wait for a lock,
not a whole transaction of several statements; they are separate from the pool's wait
for a free connection and from the connect timeout, and they do not bound a network
stall between the application and the server. Optional TCP keepalives
(`DB_KEEPALIVES*`, see keepalive_settings) let the operating system detect a dead peer on
an idle connection; there is still no read timeout for a statement in progress.
They need a transaction: under AUTOCOMMIT, which nothing here uses, they do not apply.

`database.get_connection()` does not use this pool: it still opens a new psycopg
connection for every call. Services move to the engine one at a time. The pool bounds
only this engine's connections, per process: with several worker processes the limit
applies to each, and connections opened through `get_connection()` come on top.
"""

import os
import re
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass

import psycopg
from dotenv import load_dotenv
from psycopg.pq import TransactionStatus
from sqlalchemy import URL, Connection, Engine, QueuePool, create_engine, event
from sqlalchemy.exc import SQLAlchemyError

load_dotenv()

REQUIRED_SETTINGS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER")
_WHOLE_NUMBER = re.compile(r"-?[0-9]+")


class DatabaseConfigurationError(RuntimeError):
    """A `DB_*` setting is missing or invalid. Messages name the setting, never its value."""


@dataclass(frozen=True)
class PoolSettings:
    size: int
    max_overflow: int
    timeout_seconds: int
    connect_timeout_seconds: int


@dataclass(frozen=True)
class TransactionTimeouts:
    statement_seconds: int
    lock_seconds: int


def _whole_number(environ: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = environ.get(name, "").strip()
    if not raw:
        return default
    # Plain ASCII digits only: int() alone would also accept "1_0", "+5" or non-ASCII digits.
    if not _WHOLE_NUMBER.fullmatch(raw):
        raise DatabaseConfigurationError(f"{name} must be a whole number.")
    value = int(raw)
    if not low <= value <= high:
        raise DatabaseConfigurationError(f"{name} must be between {low} and {high}.")
    return value


def database_url(environ: Mapping[str, str]) -> URL:
    """
    The connection URL. Built from parts, so no setting is ever parsed as URL syntax.

    `DB_HOST`, `DB_PORT`, `DB_NAME` and `DB_USER` are required, as the documentation and
    `.env.example` already state. Unlike `get_connection()`, which passes a missing value
    to libpq as None (so libpq picks its own default host, port, database or user), the
    engine refuses to start: a missing setting must not silently select another database.
    A blank value counts as missing. `DB_HOST` may also be a Unix-socket directory.

    `DB_PASSWORD` may be absent or empty, as with `get_connection()`: libpq then falls back
    to its own sources, such as a password file, or to password-less authentication.
    """
    missing = [name for name in REQUIRED_SETTINGS if not environ.get(name, "").strip()]
    if missing:
        raise DatabaseConfigurationError(f"Missing database settings: {', '.join(missing)}.")
    return URL.create(
        "postgresql+psycopg",
        username=environ["DB_USER"],
        password=environ.get("DB_PASSWORD") or None,
        host=environ["DB_HOST"],
        port=_whole_number(environ, "DB_PORT", 0, 1, 65535),
        database=environ["DB_NAME"],
    )


def pool_settings(environ: Mapping[str, str]) -> PoolSettings:
    """
    Pool limits from the optional `DB_POOL_*` and `DB_CONNECT_TIMEOUT_SECONDS` settings.

    The lower bounds exclude SQLAlchemy's "no limit" values (pool size 0, overflow -1),
    so the pool is always bounded. The upper bounds catch misconfiguration: 50 + 50 is
    already the whole of PostgreSQL's default max_connections (100) for one process.
    """
    return PoolSettings(
        size=_whole_number(environ, "DB_POOL_SIZE", 5, 1, 50),
        max_overflow=_whole_number(environ, "DB_POOL_MAX_OVERFLOW", 5, 0, 50),
        timeout_seconds=_whole_number(environ, "DB_POOL_TIMEOUT_SECONDS", 10, 1, 300),
        connect_timeout_seconds=_whole_number(environ, "DB_CONNECT_TIMEOUT_SECONDS", 10, 1, 300),
    )


def _optional_whole_number(
    environ: Mapping[str, str], name: str, low: int, high: int
) -> int | None:
    """Like _whole_number, but None when the setting is absent or blank."""
    if not environ.get(name, "").strip():
        return None
    return _whole_number(environ, name, 0, low, high)


# libpq connection parameters for TCP keepalives, from optional settings.
KEEPALIVE_SETTINGS = {
    "DB_KEEPALIVES": ("keepalives", 0, 1),
    "DB_KEEPALIVES_IDLE_SECONDS": ("keepalives_idle", 1, 7200),
    "DB_KEEPALIVES_INTERVAL_SECONDS": ("keepalives_interval", 1, 600),
    "DB_KEEPALIVES_COUNT": ("keepalives_count", 1, 100),
}


def keepalive_settings(environ: Mapping[str, str]) -> dict[str, int]:
    """
    libpq keepalive parameters from the optional `DB_KEEPALIVES*` settings.

    Only settings that are present are passed on; absent ones keep libpq's defaults
    (keepalives on, idle time, interval and count from the operating system). Ranges:
    `DB_KEEPALIVES` 0 or 1; `DB_KEEPALIVES_IDLE_SECONDS` 1-7200 (seconds of inactivity
    before the first probe); `DB_KEEPALIVES_INTERVAL_SECONDS` 1-600 (seconds between
    probes); `DB_KEEPALIVES_COUNT` 1-100 (unanswered probes before the connection is
    considered dead; libpq may ignore it on Windows). Setting a
    timing value while `DB_KEEPALIVES=0` is refused as contradictory.
    """
    params = {}
    for name, (parameter, low, high) in KEEPALIVE_SETTINGS.items():
        value = _optional_whole_number(environ, name, low, high)
        if value is not None:
            params[parameter] = value
    if params.get("keepalives") == 0 and len(params) > 1:
        raise DatabaseConfigurationError(
            "DB_KEEPALIVES=0 disables keepalives; remove the other DB_KEEPALIVES_* settings."
        )
    return params


def transaction_timeouts(environ: Mapping[str, str]) -> TransactionTimeouts:
    """
    PostgreSQL timeouts for every engine transaction, from the optional
    `DB_STATEMENT_TIMEOUT_SECONDS` (1-300, default 30) and `DB_LOCK_TIMEOUT_SECONDS`
    (1-60, default 5). Zero, PostgreSQL's "no limit", is not accepted.
    """
    return TransactionTimeouts(
        statement_seconds=_whole_number(environ, "DB_STATEMENT_TIMEOUT_SECONDS", 30, 1, 300),
        lock_seconds=_whole_number(environ, "DB_LOCK_TIMEOUT_SECONDS", 5, 1, 60),
    )


# Each timeout becomes the smaller of the configured limit and the session's current
# value, unless that value is 0 (disabled). pg_settings.setting holds both as integers in
# milliseconds, so no unit parsing is needed. The aggregate always yields exactly one row
# (a missing setting reads as NULL and the configured limit applies). set_config(..., true)
# is SET LOCAL: the values end with the transaction. One statement, so one round trip.
_APPLY_TIMEOUTS = """
SELECT
    pg_catalog.set_config(
        'statement_timeout',
        (CASE WHEN current.statement_ms > 0
              THEN least(current.statement_ms, %(statement_ms)s)
              ELSE %(statement_ms)s END)::text,
        true
    ),
    pg_catalog.set_config(
        'lock_timeout',
        (CASE WHEN current.lock_ms > 0
              THEN least(current.lock_ms, %(lock_ms)s)
              ELSE %(lock_ms)s END)::text,
        true
    )
FROM (
    SELECT
        max(setting::bigint) FILTER (WHERE name = 'statement_timeout') AS statement_ms,
        max(setting::bigint) FILTER (WHERE name = 'lock_timeout') AS lock_ms
    FROM pg_catalog.pg_settings
    WHERE name IN ('statement_timeout', 'lock_timeout')
) AS current
"""


class TransactionOwnershipError(RuntimeError):
    """Code inside write_transaction() tried to end the transaction the block owns."""


# Set, as an execution option of the one Connection object, while write_transaction()
# owns its transaction. Execution options belong to that Connection object, not to the
# pooled DBAPI connection, so nothing carries over to the next user of the connection.
_OWNED = "medibridge_owned_write_transaction"


def _refuse_inner_transaction_control(conn: Connection) -> None:
    # SQLAlchemy fires these events before it asks the driver to commit or roll back, so
    # raising here stops the commit or rollback from reaching PostgreSQL at all.
    if conn.get_execution_options().get(_OWNED):
        raise TransactionOwnershipError(
            "write_transaction() owns this transaction; do not commit or roll back inside it."
        )


def build_engine(environ: Mapping[str, str]) -> Engine:
    """
    A new engine for the given settings. It opens no connection until first used.

    At most `size + max_overflow` connections are open at once. A caller that finds them
    all in use waits up to `timeout_seconds`, then gets `sqlalchemy.exc.TimeoutError`.
    Every connection is tested before it is handed out (`pool_pre_ping`), so one closed
    by the server is replaced instead of failing the caller's first statement. Every
    transaction starts with the transaction-local timeouts from `transaction_timeouts`,
    or the session's own where they are stricter.

    Statement parameters are left out of SQLAlchemy's error messages and logs
    (`hide_parameters`): they can hold patient data, and an unhandled error's message
    ends up in the server log.
    """
    url = database_url(environ)
    pool = pool_settings(environ)
    timeouts = transaction_timeouts(environ)
    keepalives = keepalive_settings(environ)
    engine = create_engine(
        url,
        poolclass=QueuePool,
        pool_size=pool.size,
        max_overflow=pool.max_overflow,
        pool_timeout=pool.timeout_seconds,
        pool_pre_ping=True,
        connect_args={"connect_timeout": pool.connect_timeout_seconds, **keepalives},
        hide_parameters=True,
    )
    values = {
        "statement_ms": timeouts.statement_seconds * 1000,
        "lock_ms": timeouts.lock_seconds * 1000,
    }

    @event.listens_for(engine, "begin")
    def apply_transaction_timeouts(conn: Connection) -> None:
        # Runs when a transaction begins, before its first statement.
        conn.exec_driver_sql(_APPLY_TIMEOUTS, values).one()

    event.listen(engine, "commit", _refuse_inner_transaction_control)
    event.listen(engine, "rollback", _refuse_inner_transaction_control)
    return engine


_engine: Engine | None = None
_engine_lock = threading.Lock()


def get_engine() -> Engine:
    """
    The process-wide engine, built from `os.environ` on first use.

    The lock only guards creating the engine object, which opens no connection, so it is
    never held while waiting for the database.
    """
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = build_engine(os.environ)
        return _engine


def dispose_engine() -> None:
    """
    Closes the idle pooled connections and forgets the engine; the next `get_engine()`
    builds a new one from the current settings. Does nothing if no engine exists, so it
    never creates one, and calling it again is harmless.

    Supported use: when no database work is in progress, as at application shutdown
    (after the server has finished its requests) or test teardown. Work still running at
    that moment is not interrupted, but its connections escape the cleanup:

    - A connection checked out now goes back, when its block ends, to the pool that was
      just discarded, and is closed only when that pool is garbage-collected.
    - Code that obtained the engine before this call and connects afterwards gets a
      connection from a new pool of that old engine, which nothing will dispose.
    """
    global _engine
    with _engine_lock:
        engine, _engine = _engine, None
    if engine is not None:
        engine.dispose()


@contextmanager
def read_only_connection() -> Iterator[Connection]:
    """
    A pooled connection for reads, in a transaction that PostgreSQL itself keeps
    read-only (`postgresql_readonly`: any write fails with SQLSTATE 25006), with the
    engine's statement and lock timeouts. Everything inside one block shares that
    transaction (READ COMMITTED, PostgreSQL's default: each statement sees the data
    committed when it starts). When the block ends, normally or by an exception, the
    transaction is rolled back and the connection returns to the pool, which resets the
    read-only setting. Results must be read inside the block. After a database error
    inside the block, leave it rather than using the connection again.
    """
    with get_engine().connect() as conn:
        conn.execution_options(postgresql_readonly=True)
        yield conn


_CURRENT_XID = "SELECT pg_current_xact_id()::text"


def _discard_transaction(conn: Connection) -> None:
    """
    Ends whatever transaction is still open on `conn`, at the SQLAlchemy level and at
    the driver level: after a refused commit SQLAlchemy has already dropped its own
    transaction while PostgreSQL's is still open. A connection that cannot be cleaned
    (for example a lost one) is invalidated, so the pool never hands it out again. A
    failure here never replaces the error that is propagating.
    """
    try:
        if conn.in_transaction():
            conn.rollback()
        raw = conn.connection.driver_connection
        if raw.info.transaction_status != TransactionStatus.IDLE:
            raw.rollback()
    except SQLAlchemyError, psycopg.Error:
        with suppress(SQLAlchemyError):
            conn.invalidate()


@contextmanager
def write_transaction() -> Iterator[Connection]:
    """
    A pooled connection in one read-write transaction, with the engine's statement and
    lock timeouts. The block owns the transaction: it commits when the block ends
    normally and rolls back when an exception escapes, which then propagates unchanged
    (a SQLAlchemy error with the psycopg error in `.orig`; db_errors reads both).

    Supported guarantee: through the SQLAlchemy Connection it yields, the transaction
    cannot be ended early. `conn.commit()` and `conn.rollback()` inside the block raise
    TransactionOwnershipError before anything is sent to PostgreSQL, and the whole block
    is then rolled back, so a block's writes are committed all together or not at all.
    Savepoints (`conn.begin_nested()`) work as usual inside it.

    Unsupported bypasses: ending the transaction behind SQLAlchemy's back (the raw driver
    connection's commit(), or COMMIT / ROLLBACK sent as SQL text) cannot be intercepted.
    It is detected at the end of the block (the PostgreSQL transaction id changed), which
    then raises TransactionOwnershipError and rolls back whatever came after; what the
    bypass committed stays committed. Do not do this.

    The bypass check reads pg_current_xact_id() at the start and end, which gives even a
    block that writes nothing a transaction id (PostgreSQL 13 or later; not usable on a
    read-only standby, where writes fail anyway).

    Each block is its own transaction on its own connection; blocks do not nest. After
    a database error inside the block, leave it rather than using the connection again.
    """
    with get_engine().connect() as conn:
        conn.execution_options(**{_OWNED: True})
        transaction = conn.begin()
        try:
            start_xid = conn.exec_driver_sql(_CURRENT_XID).scalar_one()
            yield conn
            if conn.exec_driver_sql(_CURRENT_XID).scalar_one() != start_xid:
                raise TransactionOwnershipError(
                    "The transaction was ended outside SQLAlchemy inside write_transaction()."
                )
            conn.execution_options(**{_OWNED: False})
            transaction.commit()
        except BaseException:
            conn.execution_options(**{_OWNED: False})
            _discard_transaction(conn)
            raise
