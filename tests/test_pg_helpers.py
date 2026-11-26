"""
Refusal paths of the identity check in tests/pg_helpers.py.

The catalog checks (OID, owner, ownership marker) read shared catalogs, so they give the
same answer from any database on the server. Only current_database() shows which database
a connection is attached to. These tests use this session's own database and a stand-in
with another name; they never connect to any other database.
"""

from types import SimpleNamespace

import pytest

import db_engine
from tests import db_safety, pg_helpers
from tests.pg_helpers import verified_connection, verified_engine, verify_identity

# Not a database on the server: nothing ever connects to it.
OTHER_NAME = "medibridge_test_not_this_session"


def elsewhere(disposable_database):
    """This session's database identity, under a name the connection is not attached to."""
    return SimpleNamespace(name=OTHER_NAME, created=disposable_database.created)


class AttachedElsewhere:
    """
    A connection that reports another current database and passes every other query to
    this session's real connection, as a connection to another database on the same
    server would answer the shared-catalog checks.
    """

    def __init__(self, conn):
        self._conn = conn
        self.queries = []

    def execute(self, query, *args):
        self.queries.append(query)
        if query == "SELECT current_database()":
            return SimpleNamespace(fetchone=lambda: ("postgres",))
        return self._conn.execute(query, *args)


def test_this_session_database_is_accepted(disposable_database):
    with verified_connection(disposable_database) as conn:
        verify_identity(conn, disposable_database)


def test_another_current_database_is_refused_although_the_catalog_checks_pass(
    disposable_database,
):
    with verified_connection(disposable_database) as conn:
        attached_elsewhere = AttachedElsewhere(conn)

        # Precondition: the name, OID, owner and marker checks alone would accept it.
        found, role_oid = db_safety.fetch_identity(attached_elsewhere, disposable_database.name)
        db_safety.check_identity(
            session_name=disposable_database.name,
            created=disposable_database.created,
            found=found,
            current_role_oid=role_oid,
            require_marker=True,
        )

        attached_elsewhere.queries.clear()
        with pytest.raises(db_safety.UnsafeTestDatabase, match="not attached to this session"):
            verify_identity(attached_elsewhere, disposable_database)

    # Refused before anything else was asked of the connection.
    assert attached_elsewhere.queries == ["SELECT current_database()"]


def test_a_real_connection_is_refused_for_another_session_name(disposable_database):
    with verified_connection(disposable_database) as conn:
        with pytest.raises(db_safety.UnsafeTestDatabase, match="not attached to this session"):
            verify_identity(conn, elsewhere(disposable_database))


def test_verified_engine_disposes_its_engine_when_identity_is_refused(
    disposable_database, monkeypatch
):
    built = []
    real_build_engine = db_engine.build_engine

    def recording_build_engine(*args, **kwargs):
        engine = real_build_engine(*args, **kwargs)
        built.append((engine, engine.pool))
        return engine

    monkeypatch.setattr(pg_helpers.db_engine, "build_engine", recording_build_engine)

    with pytest.raises(db_safety.UnsafeTestDatabase, match="not attached to this session"):
        with verified_engine(elsewhere(disposable_database)):
            pytest.fail("The engine must not be handed out after a refusal.")

    [(engine, first_pool)] = built
    assert first_pool.checkedout() == 0  # the verifying connection was returned
    assert first_pool.checkedin() == 0  # and closed: the pool was disposed
    assert engine.pool is not first_pool
