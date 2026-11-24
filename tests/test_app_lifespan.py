"""
The application lifespan (main.lifespan) and the shared database engine (db_engine).

Contract: startup does nothing; shutdown disposes the engine if one exists and never
creates one; each lifespan is independent, so an engine created after one shutdown is
disposed by the next. tests/test_router_architecture.py checks that the lifespan creates
no engine and opens no connection when none exists.
"""

import asyncio
import time

import psycopg
import pytest
from fastapi.testclient import TestClient

import db_engine
import main
from tests import db_safety

WAIT_SECONDS = 10


class FakeEngine:
    def __init__(self):
        self.disposed = 0

    def dispose(self):
        self.disposed += 1


def run_lifespan(app, messages=None) -> list[str]:
    """Runs one ASGI lifespan (startup, then shutdown) and returns the messages sent."""
    messages = [] if messages is None else messages

    async def run():
        async def receive():
            return {"type": "lifespan.startup" if not messages else "lifespan.shutdown"}

        async def send(message):
            messages.append(message["type"])

        await app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, receive, send)

    asyncio.run(run())
    return messages


COMPLETE = ["lifespan.startup.complete", "lifespan.shutdown.complete"]


def test_shutdown_disposes_an_existing_engine_once_and_forgets_it(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(db_engine, "_engine", engine)

    assert run_lifespan(main.app) == COMPLETE

    assert engine.disposed == 1
    assert db_engine._engine is None


def test_repeated_lifespans_dispose_only_the_engine_each_one_saw(monkeypatch):
    built = []

    def build(environ):
        built.append(FakeEngine())
        return built[-1]

    monkeypatch.setattr(db_engine, "_engine", None)
    monkeypatch.setattr(db_engine, "build_engine", build)

    db_engine.get_engine()  # a request used the engine during the first lifespan
    assert run_lifespan(main.app) == COMPLETE
    assert run_lifespan(main.app) == COMPLETE  # nothing used it: nothing built or disposed
    db_engine.get_engine()
    assert run_lifespan(main.app) == COMPLETE

    assert [engine.disposed for engine in built] == [1, 1]
    assert db_engine._engine is None


def test_a_shutdown_error_from_dispose_is_not_swallowed(monkeypatch):
    def failing_dispose():
        raise RuntimeError("synthetic dispose failure")

    monkeypatch.setattr(db_engine, "dispose_engine", failing_dispose)

    messages = []
    with pytest.raises(RuntimeError, match="synthetic dispose failure"):
        run_lifespan(main.app, messages)

    # Starlette reports the failure to the server, then re-raises it.
    assert messages == ["lifespan.startup.complete", "lifespan.shutdown.failed"]


def _backend_exists(conn, pid) -> bool:
    return conn.execute("SELECT 1 FROM pg_stat_activity WHERE pid = %s", (pid,)).fetchone()


def test_shutdown_closes_the_real_pools_idle_connections(disposable_database):
    with psycopg.connect(
        **disposable_database.config.connect_kwargs(disposable_database.name), autocommit=True
    ) as observer:
        found, role_oid = db_safety.fetch_identity(observer, disposable_database.name)
        db_safety.check_identity(
            session_name=disposable_database.name,
            created=disposable_database.created,
            found=found,
            current_role_oid=role_oid,
            require_marker=True,
        )

        # The same path a server takes: TestClient as a context manager runs the lifespan.
        with TestClient(main.app):
            with db_engine.get_engine().connect() as conn:
                pid = conn.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
            assert _backend_exists(observer, pid)  # idle in the pool while the app runs

        assert db_engine._engine is None
        deadline = time.monotonic() + WAIT_SECONDS
        while _backend_exists(observer, pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _backend_exists(observer, pid)

    # The next use builds a new engine.
    with db_engine.get_engine().connect() as conn:
        assert conn.exec_driver_sql("SELECT current_database()").scalar_one() == (
            disposable_database.name
        )
