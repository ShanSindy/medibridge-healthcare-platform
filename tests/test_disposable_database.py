"""
Regression tests for the disposable-database lifecycle (create, clean, drop),
including partial setup failures.

Each test creates its own extra disposable database on the test server, on
top of the session database. The `scratch` fixture removes it afterwards,
including in the cases where the code under test correctly refuses to.
"""

import psycopg
import pytest
from psycopg import sql

from tests import db_safety
from tests.disposable_database import DisposableDatabase


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
    assert scratch.create_attempted and scratch.created is None

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

    with pytest.raises(db_safety.UnsafeTestDatabase, match="reporting.synthetic_summary"):
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
