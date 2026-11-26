"""
Helpers for tests that check database behaviour on this session's disposable database.

They add no safety of their own: every connection is opened to the session database
and verified with tests/db_safety (name, OID, owner, ownership marker) before use, and
the harness still owns creating, cleaning and dropping it.
"""

import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import psycopg
from sqlalchemy import Engine

import db_engine
from tests import db_safety

WAIT_SECONDS = 10.0


def verify_identity(conn, disposable_database) -> None:
    """Raises unless `conn` is connected to this session's disposable database."""
    connected_to = conn.execute("SELECT current_database()").fetchone()[0]
    if connected_to != disposable_database.name:
        raise db_safety.UnsafeTestDatabase(
            "The connection is not attached to this session's test database."
        )
    found, role_oid = db_safety.fetch_identity(conn, disposable_database.name)
    db_safety.check_identity(
        session_name=disposable_database.name,
        created=disposable_database.created,
        found=found,
        current_role_oid=role_oid,
        require_marker=True,
    )


@contextmanager
def verified_connection(disposable_database) -> Iterator[psycopg.Connection]:
    """An autocommit psycopg connection to this session's database, verified, then closed."""
    conn = psycopg.connect(
        **disposable_database.config.connect_kwargs(disposable_database.name), autocommit=True
    )
    try:
        verify_identity(conn, disposable_database)
        yield conn
    finally:
        conn.close()


@contextmanager
def verified_engine(disposable_database, **settings: str) -> Iterator[Engine]:
    """
    A separate engine for this session's database with some DB_* settings overridden (for
    example a one-connection pool), verified to reach this session's database before
    use and disposed afterwards.
    """
    engine = db_engine.build_engine({**os.environ, **settings})
    try:
        with engine.connect() as conn:
            verify_identity(conn.connection.driver_connection, disposable_database)
        yield engine
    finally:
        engine.dispose()


def backend_exists(conn, pid: int) -> bool:
    """Whether PostgreSQL still has a backend with this process id."""
    row = conn.execute("SELECT 1 FROM pg_stat_activity WHERE pid = %s", (pid,)).fetchone()
    return row is not None


def wait_until(condition: Callable[[], bool], seconds: float = WAIT_SECONDS) -> bool:
    """Polls `condition` until it is true or `seconds` have passed; returns its last value."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()
