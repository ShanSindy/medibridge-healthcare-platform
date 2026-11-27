"""
The Alembic baseline (ADR 0003) and the read-only migration-state inspection.

Every Alembic run uses its own fresh disposable database, created and dropped through the
test harness (name pattern, recorded identity, ownership marker), never the session
database or any other. The session database, built by migrate.py, is the historical
reference. Changes made to it here run inside transactions that are rolled back.
"""

import argparse
import configparser
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import psycopg
import pytest
from alembic.config import Config

import db_schema
import schema_inspection as si
from alembic import command
from tests import db_safety
from tests.disposable_database import DisposableDatabase
from tests.pg_helpers import verified_connection, verified_engine, wait_until

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = "schema_expected"


@pytest.fixture
def fresh_db(test_database_config):
    """A new, empty disposable database, dropped afterwards through the harness."""
    database = DisposableDatabase(test_database_config, db_safety.new_database_name())
    try:
        database.create()
        yield database
    finally:
        database.drop()
        assert database.dropped


def upgrade(database, session_sql=(), **attributes):
    """`alembic upgrade head` on `database` through an identity-verified engine."""
    config = Config(str(ROOT / "alembic.ini"))
    with verified_engine(database, DB_NAME=database.name) as engine:
        with engine.connect() as connection:
            for statement in session_sql:  # session settings, committed before the run
                connection.exec_driver_sql(statement)
            connection.commit()
            config.attributes["connection"] = connection
            config.attributes["database_name"] = attributes.get("database_name", database.name)
            command.upgrade(config, "head")
            connection.commit()


@contextmanager
def transaction(database, read_only=False):
    with verified_connection(database) as conn:
        conn.autocommit = False
        try:
            if read_only:
                conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL lock_timeout = '5s'")
            yield conn
        finally:
            conn.rollback()


@pytest.fixture
def expected(disposable_database):
    """The metadata's schema, materialized in a rolled-back scratch schema of the session DB."""
    with transaction(disposable_database) as conn:
        si.materialize_expected(conn, db_schema.metadata, SCRATCH)
        return si.snapshot(conn, SCRATCH)


def public(database, exclude):
    with transaction(database, read_only=True) as conn:
        return si.snapshot(conn, "public", exclude), si.extension_inventory(conn)


# --- fresh installation ----------------------------------------------------------------


def test_the_baseline_builds_the_historical_schema(disposable_database, fresh_db):
    upgrade(fresh_db)

    alembic_schema, alembic_extensions = public(fresh_db, (si.ALEMBIC_TABLE,))
    historical_schema, historical_extensions = public(disposable_database, (si.LEDGER_TABLE,))
    assert si.differences(historical_schema, alembic_schema) == []
    assert alembic_extensions == historical_extensions == si.EXPECTED_EXTENSIONS


def test_the_baseline_records_only_its_alembic_version(fresh_db):
    upgrade(fresh_db)

    with transaction(fresh_db, read_only=True) as conn:
        assert si.public_relations(conn) == set(db_schema.metadata.tables) | {si.ALEMBIC_TABLE}
        assert si._table_columns(conn, si.ALEMBIC_TABLE) == si.ALEMBIC_COLUMNS
        assert [r[0] for r in conn.execute("SELECT version_num FROM alembic_version")] == [
            si.ALEMBIC_BASELINE
        ]


def test_the_baseline_schema_enforces_identity_and_overlap_rules(fresh_db):
    upgrade(fresh_db)

    with transaction(fresh_db) as conn:
        with pytest.raises(psycopg.errors.GeneratedAlways):
            conn.execute(
                "INSERT INTO patients (id, first_name, last_name, date_of_birth, gender) "
                "VALUES (1, 'Synthetic', 'Explicit', '1990-01-01', 'other')"
            )
    with transaction(fresh_db) as conn:
        patient = conn.execute(
            "INSERT INTO patients (first_name, last_name, date_of_birth, gender) "
            "VALUES ('Synthetic', 'Overlap', '1990-01-01', 'other') RETURNING id"
        ).fetchone()[0]
        provider = conn.execute(
            "INSERT INTO providers (first_name, last_name, specialty) "
            "VALUES ('Dana', 'Synthetica', 'Synthetic Medicine') RETURNING id"
        ).fetchone()[0]
        insert = (
            "INSERT INTO appointments (patient_id, provider_id, starts_at, ends_at) "
            "VALUES (%s, %s, %s, %s)"
        )
        conn.execute(insert, (patient, provider, "2030-01-01 09:00+00", "2030-01-01 10:00+00"))
        with pytest.raises(psycopg.errors.ExclusionViolation):
            conn.execute(insert, (patient, provider, "2030-01-01 09:30+00", "2030-01-01 10:30+00"))


def test_a_repeated_upgrade_changes_nothing(fresh_db):
    upgrade(fresh_db)
    before = public(fresh_db, ())

    upgrade(fresh_db)

    assert public(fresh_db, ()) == before


def cli_config(expected_database=None):
    config = Config(str(ROOT / "alembic.ini"))
    x = [f"expected_database={expected_database}"] if expected_database else []
    config.cmd_opts = argparse.Namespace(x=x)
    return config


def test_the_command_line_path_uses_the_db_settings(fresh_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", fresh_db.name)

    command.upgrade(cli_config(fresh_db.name), "head")

    with transaction(fresh_db, read_only=True) as conn:
        assert si.ALEMBIC_TABLE in si.public_relations(conn)


@pytest.mark.parametrize("expected", [None, "medibridge_test_0000000000000000"])
def test_the_command_line_needs_the_expected_database_named(fresh_db, monkeypatch, expected):
    monkeypatch.setenv("DB_NAME", fresh_db.name)

    with pytest.raises(Exception, match="expected_database") as raised:
        command.upgrade(cli_config(expected), "head")

    assert type(raised.value).__name__ == "MigrationRefused"
    with transaction(fresh_db, read_only=True) as conn:
        assert si.is_empty(conn)


def test_a_hostile_session_search_path_still_builds_in_public(disposable_database, fresh_db):
    upgrade(fresh_db, session_sql=["SET search_path = missing_schema, pg_temp"])

    alembic_schema, _ = public(fresh_db, (si.ALEMBIC_TABLE,))
    historical_schema, _ = public(disposable_database, (si.LEDGER_TABLE,))
    assert si.differences(historical_schema, alembic_schema) == []


# --- refusals: nothing is changed --------------------------------------------------------


def refused(database, **attributes):
    with pytest.raises(Exception) as raised:  # noqa: PT011 - checked by type name below
        upgrade(database, **attributes)
    assert type(raised.value).__name__ == "MigrationRefused", raised.value
    return str(raised.value)


def test_a_database_built_by_migrate_py_is_not_adopted(disposable_database):
    before = public(disposable_database, ())

    message = refused(disposable_database)

    assert "not supported" in message
    assert public(disposable_database, ()) == before  # no alembic_version, nothing else


def test_a_partial_database_is_refused(fresh_db):
    with verified_connection(fresh_db) as conn:
        conn.execute("CREATE TABLE patients (id integer)")
    before = public(fresh_db, ())

    refused(fresh_db)

    assert public(fresh_db, ()) == before


@pytest.mark.parametrize(
    "setup",
    [
        ["CREATE FUNCTION public.stray() RETURNS integer LANGUAGE sql AS 'SELECT 1'"],
        ["CREATE SCHEMA elsewhere", "CREATE TABLE elsewhere.patients (id integer)"],
        ["CREATE SCHEMA elsewhere"],
        ["CREATE SEQUENCE public.stray_sequence"],
    ],
    ids=["function-only", "tables-in-another-schema", "empty-extra-schema", "sequence-only"],
)
def test_a_database_with_anything_in_it_is_not_fresh(fresh_db, expected, setup):
    with verified_connection(fresh_db) as conn:
        for statement in setup:
            conn.execute(statement)

    refused(fresh_db)

    with transaction(fresh_db, read_only=True) as conn:
        assert si.ALEMBIC_TABLE not in si.public_relations(conn)
        assert not si.is_empty(conn)
    assert state_of(fresh_db, expected).state == si.UNSUPPORTED


def test_an_unexpected_alembic_version_is_refused(fresh_db):
    upgrade(fresh_db)
    with verified_connection(fresh_db) as conn:
        conn.execute("UPDATE alembic_version SET version_num = 'unknown_revision'")

    refused(fresh_db)


def test_the_wrong_database_is_refused(fresh_db):
    refused(fresh_db, database_name="medibridge_test_0000000000000000")

    with transaction(fresh_db, read_only=True) as conn:
        assert si.public_relations(conn) == set()


def test_a_concurrent_migration_is_refused_without_waiting(fresh_db):
    with verified_connection(fresh_db) as holder:
        holder.execute("SELECT pg_advisory_lock(%s)", (si.MIGRATION_LOCK_KEY,))
        message = refused(fresh_db)
        holder.execute("SELECT pg_advisory_unlock(%s)", (si.MIGRATION_LOCK_KEY,))
    assert "lock" in message
    with transaction(fresh_db, read_only=True) as conn:
        assert si.public_relations(conn) == set()
    upgrade(fresh_db)  # the lock is free again


def test_a_failing_statement_rolls_the_whole_baseline_back(fresh_db, monkeypatch):
    # The extension step fails as it would for a role without permission to create it.
    real_execute = psycopg.Connection.execute
    executed = []

    def failing(self, query, *args, **kwargs):
        if isinstance(query, str) and "CREATE EXTENSION" in query:
            raise psycopg.errors.InsufficientPrivilege("synthetic: permission denied")
        if isinstance(query, str) and "CREATE TABLE" in query:
            executed.append(query)
        return real_execute(self, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", failing)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        upgrade(fresh_db)
    monkeypatch.undo()
    assert len(executed) == 5  # migrations 000-004 ran before the failing 005

    with transaction(fresh_db, read_only=True) as conn:
        assert si.public_relations(conn) == set()  # tables 000-004 and the version row too
    upgrade(fresh_db)


def load_baseline():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "baseline_under_test", ROOT / "alembic" / "versions" / "0001_baseline.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_baseline_pins_every_historical_migration(tmp_path):
    baseline = load_baseline()
    copy = tmp_path / "migrations"
    shutil.copytree(ROOT / "migrations", copy)
    assert len(baseline.verified_sql(copy)) == len(si.HISTORICAL_MIGRATIONS)
    assert list(baseline.PINNED) == list(si.HISTORICAL_MIGRATIONS)

    (copy / "004_create_appointments.sql").write_text("-- changed\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"004_create_appointments\.sql differs"):
        baseline.verified_sql(copy)

    (copy / "008_extra.sql").write_text("SELECT 1;\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="pinned set"):
        baseline.verified_sql(copy)


def test_the_baseline_cannot_be_downgraded():
    with pytest.raises(NotImplementedError):
        load_baseline().downgrade()


def test_no_credentials_in_configuration_or_refusals(disposable_database):
    parser = configparser.ConfigParser()
    parser.read(ROOT / "alembic.ini", encoding="utf-8")
    assert not any(parser.has_option(s, "sqlalchemy.url") for s in parser.sections())
    message = refused(disposable_database)
    password = disposable_database.config.password
    assert not password or password not in message
    assert disposable_database.config.user not in message


# --- read-only migration-state inspection ------------------------------------------------


def state_of(database, expected, *, changes=(), name=None):
    """Inspects `database` after `changes`, in one transaction that is rolled back."""
    with transaction(database) as conn:
        for statement in changes:
            conn.execute(statement)
        conn.execute("SET TRANSACTION READ ONLY")  # the inspection itself cannot write
        before = si.snapshot(conn, "public")
        state = si.inspect_migration_state(
            conn, database_name=name or database.name, expected=expected
        )
        assert si.snapshot(conn, "public") == before  # inspection changed nothing
        return state


def test_a_historical_database_is_reported_eligible_but_not_adopted(disposable_database, expected):
    state = state_of(disposable_database, expected)

    assert state == si.MigrationState(si.ELIGIBLE, ())
    assert "not-adopted" in state.state
    with transaction(disposable_database, read_only=True) as conn:
        assert si.ALEMBIC_TABLE not in si.public_relations(conn)


@pytest.mark.parametrize(
    ("changes", "state"),
    [
        (
            [
                "DELETE FROM schema_migrations "
                "WHERE filename = '005_prevent_appointment_overlaps.sql'"
            ],
            si.INVALID_LEDGER,
        ),
        (
            ["INSERT INTO schema_migrations (filename) VALUES ('008_unknown.sql')"],
            si.INVALID_LEDGER,
        ),
        (["ALTER TABLE schema_migrations ADD COLUMN note text"], si.INVALID_LEDGER),
        (["ALTER TABLE providers ALTER COLUMN phone TYPE varchar(40)"], si.SCHEMA_DRIFT),
        (["CREATE TABLE extra_table (id integer)"], si.SCHEMA_DRIFT),
        (["CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)"], si.UNSUPPORTED),
        (["DROP TABLE schema_migrations"], si.UNSUPPORTED),
    ],
    ids=[
        "missing-ledger-entry",
        "unexpected-ledger-entry",
        "ledger-structure",
        "drift",
        "unexpected-object",
        "both-ledgers",
        "no-ledger",
    ],
)
def test_invalid_historical_states_are_never_eligible(
    disposable_database, expected, changes, state
):
    assert state_of(disposable_database, expected, changes=changes).state == state


def test_fresh_and_alembic_states(fresh_db, expected):
    assert state_of(fresh_db, expected).state == si.FRESH
    upgrade(fresh_db)
    assert state_of(fresh_db, expected) == si.MigrationState(si.ALEMBIC_BASELINE_STATE, ())
    changed = ["UPDATE alembic_version SET version_num = 'unknown_revision'"]
    assert state_of(fresh_db, expected, changes=changed).state == si.UNSUPPORTED
    drifted = ["DROP INDEX idx_users_role"]
    assert state_of(fresh_db, expected, changes=drifted).state == si.SCHEMA_DRIFT


def test_inspection_refuses_the_wrong_database(disposable_database, expected):
    with pytest.raises(si.UnsafeDatabaseState):
        state_of(disposable_database, expected, name="medibridge_test_0000000000000000")


def test_inspection_tooling_imports_without_an_engine_or_connection():
    code = "; ".join(
        [
            "import psycopg, sqlalchemy",
            "psycopg.connect = sqlalchemy.create_engine = None",
            "import schema_inspection, sys",
            "assert 'db_engine' not in sys.modules and 'database' not in sys.modules",
        ]
    )
    result = subprocess.run(  # noqa: S603 - fixed arguments, no untrusted input
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr[-500:]


# --- remediation: safety paths -----------------------------------------------------------


def test_offline_mode_is_refused():
    config = Config(str(ROOT / "alembic.ini"))
    with pytest.raises(Exception, match="Offline") as raised:
        command.upgrade(config, "head", sql=True)
    assert type(raised.value).__name__ == "MigrationRefused"


def test_a_caller_connection_already_in_a_transaction_is_refused(fresh_db):
    config = Config(str(ROOT / "alembic.ini"))
    with verified_engine(fresh_db, DB_NAME=fresh_db.name) as engine:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")  # opens a transaction
            config.attributes["connection"] = connection
            config.attributes["database_name"] = fresh_db.name
            with pytest.raises(Exception, match="already be in a transaction"):
                command.upgrade(config, "head")
            connection.rollback()
    with transaction(fresh_db, read_only=True) as conn:
        assert si.is_empty(conn)


@pytest.mark.parametrize(
    "drift",
    [
        "ALTER TABLE providers ALTER COLUMN phone TYPE varchar(40)",
        "DROP INDEX idx_users_role",
        "CREATE TABLE public.extra_table (id integer)",
        "ALTER TABLE patients ADD COLUMN nickname text",
    ],
    ids=["column-type", "dropped-index", "extra-table", "extra-column"],
)
def test_a_drifted_baseline_is_refused_on_rerun(fresh_db, drift):
    upgrade(fresh_db)
    with verified_connection(fresh_db) as conn:
        conn.execute(drift)
    before = public(fresh_db, ())

    message = refused(fresh_db)

    assert "does not match" in message or "not at the Alembic baseline" in message
    assert public(fresh_db, ()) == before


def test_a_baseline_that_differs_from_the_metadata_is_never_committed(fresh_db, monkeypatch):
    changed = db_schema.build_metadata()
    changed.tables["providers"].c.phone.type.length = 40
    monkeypatch.setattr(db_schema, "metadata", changed)

    message = refused(fresh_db)

    assert "nothing was committed" in message
    with transaction(fresh_db, read_only=True) as conn:
        assert si.is_empty(conn)  # the whole baseline was rolled back


def test_verification_leaves_no_scratch_schema(fresh_db):
    upgrade(fresh_db)
    with transaction(fresh_db) as conn:
        assert si.verify_against_metadata(conn, db_schema.metadata) == []
        assert si.non_system_schemas(conn) == ["public"]


def migrate_on(database, monkeypatch):
    import migrate

    monkeypatch.setenv("DB_NAME", database.name)
    migrate.apply_migrations()


def lock_is_free(database) -> bool:
    """Whether the migration lock can be taken (polled: a closed session ends asynchronously)."""

    def free():
        with verified_connection(database) as conn:
            got = conn.execute("SELECT pg_try_advisory_lock(%s)", (si.MIGRATION_LOCK_KEY,))
            if got.fetchone()[0]:
                conn.execute("SELECT pg_advisory_unlock(%s)", (si.MIGRATION_LOCK_KEY,))
                return True
            return False

    return wait_until(free)


def test_migrate_py_still_builds_a_fresh_database_and_releases_the_lock(fresh_db, monkeypatch):
    migrate_on(fresh_db, monkeypatch)

    assert lock_is_free(fresh_db)
    with transaction(fresh_db, read_only=True) as conn:
        ledger = [r[0] for r in conn.execute("SELECT filename FROM schema_migrations ORDER BY 1")]
    assert ledger == sorted(si.HISTORICAL_MIGRATIONS)


def test_migrate_py_refuses_an_alembic_managed_database(fresh_db, monkeypatch):
    upgrade(fresh_db)
    before = public(fresh_db, ())

    with pytest.raises(Exception, match="managed by Alembic") as raised:
        migrate_on(fresh_db, monkeypatch)

    assert type(raised.value).__name__ == "MigrationRefused"
    assert public(fresh_db, ()) == before  # no schema_migrations, nothing applied
    assert lock_is_free(fresh_db)  # released on the refusal path too


def test_an_open_alembic_transaction_lock_blocks_migrate_py(fresh_db, monkeypatch):
    with verified_connection(fresh_db) as holder:
        holder.autocommit = False  # the lock Alembic holds: transaction-scoped
        holder.execute("SELECT pg_advisory_xact_lock(%s)", (si.MIGRATION_LOCK_KEY,))
        with pytest.raises(Exception, match="migration lock") as raised:
            migrate_on(fresh_db, monkeypatch)
        assert type(raised.value).__name__ == "MigrationRefused"
        holder.rollback()
    with transaction(fresh_db, read_only=True) as conn:
        assert si.is_empty(conn)
    assert lock_is_free(fresh_db)


@pytest.mark.parametrize(
    "change",
    [
        "ALTER TABLE alembic_version ADD COLUMN note text",
        "CREATE INDEX extra_version_idx ON alembic_version (version_num)",
    ],
    ids=["extra-column", "extra-index"],
)
def test_a_tampered_version_table_is_refused_on_rerun(fresh_db, change):
    upgrade(fresh_db)
    with verified_connection(fresh_db) as conn:
        conn.execute(change)
    before = public(fresh_db, ())

    refused(fresh_db)

    assert public(fresh_db, ()) == before


def test_migrate_py_and_alembic_exclude_each_other(fresh_db, monkeypatch):
    with verified_connection(fresh_db) as holder:  # stands in for the other tool
        holder.execute("SELECT pg_advisory_lock(%s)", (si.MIGRATION_LOCK_KEY,))
        with pytest.raises(Exception, match="migration lock") as raised:
            migrate_on(fresh_db, monkeypatch)
        assert type(raised.value).__name__ == "MigrationRefused"
        assert "lock" in refused(fresh_db)
    with transaction(fresh_db, read_only=True) as conn:
        assert si.is_empty(conn)  # neither tool changed anything while the lock was held


def test_a_missing_historical_file_stops_the_baseline(tmp_path):
    baseline = load_baseline()
    copy = tmp_path / "migrations"
    shutil.copytree(ROOT / "migrations", copy)
    (copy / "006_create_users.sql").unlink()

    with pytest.raises(RuntimeError, match="pinned set"):
        baseline.verified_sql(copy)


def test_the_baseline_reads_its_files_independently_of_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    baseline = load_baseline()
    assert baseline.MIGRATIONS_DIR == ROOT / "migrations"
    assert len(baseline.verified_sql()) == len(si.HISTORICAL_MIGRATIONS)


def test_line_endings_do_not_change_the_pinned_hashes(tmp_path):
    baseline = load_baseline()
    copy = tmp_path / "migrations"
    copy.mkdir()
    for name in si.HISTORICAL_MIGRATIONS:
        text = (ROOT / "migrations" / name).read_text(encoding="utf-8-sig")
        lf = text.replace("\r\n", "\n")
        target = copy / name
        if name.startswith("00") and int(name[2]) % 2:  # alternate CRLF and LF files
            target.write_bytes(lf.replace("\n", "\r\n").encode())
        else:
            target.write_bytes(lf.encode())
    assert len(baseline.verified_sql(copy)) == len(si.HISTORICAL_MIGRATIONS)
