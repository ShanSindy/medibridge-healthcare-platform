"""
Regression tests for the disposable-database lifecycle (create, clean, drop),
including partial setup failures.

Each test creates its own extra disposable database on the test server, on
top of the session database. The `scratch` fixture removes it afterwards,
including in the cases where the code under test correctly refuses to.
"""

import os
import sys
import time

import psycopg
import pytest
from psycopg import sql

import db_engine
from tests import db_safety
from tests.disposable_database import DisposableDatabase, release_application_pool


def _admin(config) -> psycopg.Connection:
    return psycopg.connect(**config.connect_kwargs(config.maintenance_db), autocommit=True)


def _identity(config, name):
    """(oid, comment) of the database with this name, or None."""
    with _admin(config) as admin:
        return admin.execute(
            "SELECT oid, shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = %s",
            (name,),
        ).fetchone()


@pytest.fixture
def scratch(test_database_config):
    database = DisposableDatabase(test_database_config, db_safety.new_database_name())
    yield database
    if database.cleanup_connection is not None:
        database.cleanup_connection.close()
    # Test-side cleanup: the name was generated above, with 64 random bits,
    # and is never the session database.
    assert db_safety.is_disposable_name(database.name)
    assert not db_safety.is_session_database(database.name)
    with _admin(test_database_config) as admin:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database.name))
        )


def test_normal_lifecycle_records_identity_marks_and_drops(scratch, test_database_config):
    scratch.create()

    oid, comment = _identity(test_database_config, scratch.name)
    assert scratch.created.oid == oid
    assert comment == db_safety.OWNERSHIP_MARKER
    assert scratch.marker_written

    scratch.drop()

    assert scratch.dropped
    assert _identity(test_database_config, scratch.name) is None


def test_marker_failure_still_drops_the_database_this_process_created(
    scratch, test_database_config, monkeypatch
):
    def failing_marker(admin):
        raise RuntimeError("simulated COMMENT ON DATABASE failure")

    monkeypatch.setattr(scratch, "_write_marker", failing_marker)

    with pytest.raises(RuntimeError, match="simulated"):
        scratch.create()

    oid, comment = _identity(test_database_config, scratch.name)
    assert comment is None  # created, but never marked
    assert scratch.created.oid == oid
    assert not scratch.marker_written

    scratch.drop()  # allowed: same name, OID and owner as recorded at creation

    assert scratch.dropped
    assert _identity(test_database_config, scratch.name) is None


def test_unrecorded_identity_means_the_database_is_not_dropped(
    scratch, test_database_config, monkeypatch
):
    def failing_identity(admin):
        raise RuntimeError("simulated failure before the OID was recorded")

    monkeypatch.setattr(scratch, "_record_identity", failing_identity)

    with pytest.raises(RuntimeError, match="simulated"):
        scratch.create()
    assert scratch.create_attempted
    assert scratch.created is None

    with pytest.raises(db_safety.UnsafeTestDatabase, match="ownership cannot be established"):
        scratch.drop()

    assert not scratch.dropped
    assert "ownership cannot be established" in scratch.drop_error
    assert _identity(test_database_config, scratch.name) is not None  # left in place and reported


def test_a_database_replaced_under_the_same_name_is_not_dropped(scratch, test_database_config):
    scratch.create()
    original_oid = scratch.created.oid
    with _admin(test_database_config) as admin:
        name = sql.Identifier(scratch.name)
        admin.execute(sql.SQL("DROP DATABASE {}").format(name))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(name))
        admin.execute(
            sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                name, sql.Literal(db_safety.OWNERSHIP_MARKER)
            )
        )
    assert _identity(test_database_config, scratch.name)[0] != original_oid

    with pytest.raises(db_safety.UnsafeTestDatabase, match="OID changed"):
        scratch.drop()

    assert _identity(test_database_config, scratch.name) is not None


def test_a_removed_marker_blocks_the_drop(scratch, test_database_config):
    scratch.create()
    with _admin(test_database_config) as admin:
        admin.execute(
            sql.SQL("COMMENT ON DATABASE {} IS NULL").format(sql.Identifier(scratch.name))
        )

    with pytest.raises(db_safety.UnsafeTestDatabase, match="ownership marker"):
        scratch.drop()

    assert _identity(test_database_config, scratch.name) is not None


def test_cleanup_refuses_a_connection_to_another_database(scratch, test_database_config):
    scratch.create()
    scratch.cleanup_connection = psycopg.connect(
        **test_database_config.connect_kwargs(test_database_config.maintenance_db), autocommit=True
    )

    with pytest.raises(db_safety.UnsafeTestDatabase, match="not attached"):
        scratch.clean()


def test_tables_outside_the_supported_schema_stop_cleanup(scratch):
    scratch.create()
    scratch.open_cleanup_connection()
    conn = scratch.cleanup_connection
    conn.execute("CREATE TABLE public.synthetic_rows (id integer)")
    conn.execute("INSERT INTO public.synthetic_rows VALUES (1)")
    conn.execute("CREATE SCHEMA reporting")
    conn.execute("CREATE TABLE reporting.synthetic_summary (id integer)")

    with pytest.raises(db_safety.UnsafeTestDatabase, match=r"reporting\.synthetic_summary"):
        scratch.clean()

    # Nothing was truncated: the run stops instead of cleaning partially.
    assert conn.execute("SELECT count(*) FROM public.synthetic_rows").fetchone()[0] == 1


def test_tables_created_after_setup_are_cleaned_too(scratch):
    scratch.create()
    scratch.open_cleanup_connection()
    conn = scratch.cleanup_connection
    conn.execute("CREATE TABLE public.first_table (id integer PRIMARY KEY)")
    conn.execute("CREATE TABLE public.added_later (id integer REFERENCES public.first_table (id))")
    conn.execute("INSERT INTO public.first_table VALUES (1)")
    conn.execute("INSERT INTO public.added_later VALUES (1)")

    scratch.clean()

    assert conn.execute("SELECT count(*) FROM public.first_table").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM public.added_later").fetchone()[0] == 0


# --- the application's pool and the session database --------------------------------------
#
# The session database cannot be dropped mid-run, so a scratch database stands in for it:
# is_session_database() is patched to name the scratch database, and the application's
# engine (db_engine) is pointed at it.


def _backend_exists(config, pid) -> bool:
    with _admin(config) as admin:
        return admin.execute("SELECT 1 FROM pg_stat_activity WHERE pid = %s", (pid,)).fetchone()


def _wait_until(condition, seconds=10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


@pytest.fixture
def scratch_as_session(scratch, monkeypatch, test_database_config):
    """The scratch database, created, posing as the session database with an idle app pool."""
    scratch.create()
    monkeypatch.setattr(db_safety, "is_session_database", lambda name: name == scratch.name)
    engine = db_engine.build_engine({**os.environ, "DB_NAME": scratch.name})
    monkeypatch.setattr(db_engine, "_engine", engine)
    try:
        with engine.connect() as conn:
            pid = conn.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
        assert _backend_exists(test_database_config, pid)  # idle in the pool
        yield scratch, pid
    finally:
        engine.dispose()
        # Undo the patches here rather than relying on fixture teardown order: the
        # `scratch` teardown asserts that its database is not the session database.
        monkeypatch.undo()


def test_dropping_the_session_database_first_closes_the_application_pool(
    scratch_as_session, monkeypatch, test_database_config
):
    scratch, pid = scratch_as_session
    real_dispose = db_engine.dispose_engine
    observed = []

    def observing_dispose():
        real_dispose()
        # Still before DROP DATABASE ... WITH (FORCE): the database exists, and the pooled
        # backend ends because dispose closed it, not because the drop forced it.
        observed.append(
            (
                _identity(test_database_config, scratch.name) is not None,
                _wait_until(lambda: not _backend_exists(test_database_config, pid)),
            )
        )

    monkeypatch.setattr(db_engine, "dispose_engine", observing_dispose)

    scratch.drop()

    assert observed == [(True, True)]
    assert scratch.dropped
    assert db_engine._engine is None


def test_a_failed_pool_release_still_drops_and_is_then_reported(
    scratch_as_session, monkeypatch, test_database_config
):
    scratch, _ = scratch_as_session

    def failing_dispose():
        raise RuntimeError("synthetic release failure")

    monkeypatch.setattr(db_engine, "dispose_engine", failing_dispose)

    with pytest.raises(RuntimeError, match="synthetic release failure"):
        scratch.drop()

    # The identity-checked drop ran anyway; the release failure is not swallowed.
    assert scratch.dropped
    assert _identity(test_database_config, scratch.name) is None
    assert scratch.drop_error == "pool release failed: RuntimeError: synthetic release failure"


def test_a_refused_drop_after_a_failed_release_reports_both(
    scratch_as_session, monkeypatch, test_database_config
):
    scratch, _ = scratch_as_session

    def failing_dispose():
        raise RuntimeError("synthetic release failure")

    monkeypatch.setattr(db_engine, "dispose_engine", failing_dispose)
    with _admin(test_database_config) as admin:
        admin.execute(
            sql.SQL("COMMENT ON DATABASE {} IS NULL").format(sql.Identifier(scratch.name))
        )

    with pytest.raises(db_safety.UnsafeTestDatabase, match="ownership marker") as raised:
        scratch.drop()

    # The safety refusal wins (nothing dropped) and still mentions the release failure.
    assert not scratch.dropped
    assert _identity(test_database_config, scratch.name) is not None
    assert any("synthetic release failure" in note for note in raised.value.__notes__)


def test_dropping_another_database_leaves_the_application_pool_alone(scratch, monkeypatch):
    calls = []
    monkeypatch.setattr(db_engine, "dispose_engine", lambda: calls.append("dispose"))
    scratch.create()

    scratch.drop()

    assert scratch.dropped
    assert calls == []


def test_releasing_without_the_engine_module_imports_nothing(monkeypatch):
    monkeypatch.delitem(sys.modules, "db_engine")

    release_application_pool()

    assert "db_engine" not in sys.modules
