"""
The shared SQLAlchemy engine: one bounded PostgreSQL connection pool per process.

Ownership. The engine belongs to the process, not to a request or to the FastAPI
application object, because services reach it directly through `get_engine()`. It is
created on first use (never at import or application startup) from the `DB_*` settings,
and kept until `dispose_engine()`, which the application calls at shutdown (main.py) and
the test harness calls before it drops its database.

Code that uses it owns each connection through a `with` block:

    with get_engine().connect() as conn:  # reads; rolled back when the block ends
        ...
    with get_engine().begin() as conn:    # writes; committed, or rolled back on error
        ...

When the block ends the connection goes back to the pool, which rolls back anything
still open before the connection is handed out again. Nothing is committed implicitly
by `connect()`.

`database.get_connection()` does not use this pool: it still opens a new psycopg
connection for every call. Services move to the engine one at a time. The pool bounds
only this engine's connections, per process: with several worker processes the limit
applies to each, and connections opened through `get_connection()` come on top.
"""

import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv
from sqlalchemy import URL, Engine, QueuePool, create_engine

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


def build_engine(environ: Mapping[str, str]) -> Engine:
    """
    A new engine for the given settings. It opens no connection until first used.

    At most `size + max_overflow` connections are open at once. A caller that finds them
    all in use waits up to `timeout_seconds`, then gets `sqlalchemy.exc.TimeoutError`.
    Every connection is tested before it is handed out (`pool_pre_ping`), so one closed
    by the server is replaced instead of failing the caller's first statement.

    Statement parameters are left out of SQLAlchemy's error messages and logs
    (`hide_parameters`): they can hold patient data, and an unhandled error's message
    ends up in the server log.
    """
    url = database_url(environ)
    pool = pool_settings(environ)
    return create_engine(
        url,
        poolclass=QueuePool,
        pool_size=pool.size,
        max_overflow=pool.max_overflow,
        pool_timeout=pool.timeout_seconds,
        pool_pre_ping=True,
        connect_args={"connect_timeout": pool.connect_timeout_seconds},
        hide_parameters=True,
    )


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
