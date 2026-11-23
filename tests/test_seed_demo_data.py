"""
seed_demo_data.py against this session's disposable database: dataset consistency, target
refusals (configuration, protected names, connected identity, authorization marker),
committed records visible through the existing APIs, repeat refusal, dry run, rollback,
lock timeout, commit-outcome reporting and a concurrent initializer.

The synthetic-demo authorization marker (a comment on the public schema) is set only on
this session's verified disposable database, through the `authorized` fixture, and the
previous comment is restored afterwards. The lifecycle's own marker (the database comment)
is never touched.
"""

import contextlib
import dataclasses
import io
import os
import queue
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import errors, sql

import main as app_main
import seed_demo_data
from auth_dependencies import get_current_user
from tests import db_safety
from user_service import create_user

ANCHOR = "2031-01-06T00:00:00+00:00"
COUNTS = {"patients": 16, "patient_identifiers": 16, "providers": 6, "appointments": 20}
HOLDER_IDLE_LIMIT = "15s"  # server-side bound on every transaction a test holds open


def verified_connection(disposable_database, *, autocommit):
    """A connection to this session's disposable database; closed if verification fails."""
    conn = psycopg.connect(
        **disposable_database.config.connect_kwargs(disposable_database.name),
        autocommit=autocommit,
    )
    try:
        found, role_oid = db_safety.fetch_identity(conn, disposable_database.name)
        db_safety.check_identity(
            session_name=disposable_database.name,
            created=disposable_database.created,
            found=found,
            current_role_oid=role_oid,
            require_marker=True,
        )
        if not autocommit:
            conn.commit()
    except BaseException:
        conn.close()
        raise
    return conn


@pytest.fixture
def db(disposable_database):
    opened = []

    def open_verified():
        conn = verified_connection(disposable_database, autocommit=True)
        opened.append(conn)
        return conn

    yield open_verified
    for conn in opened:
        conn.close()


def set_public_schema_comment(disposable_database, value):
    conn = verified_connection(disposable_database, autocommit=True)
    try:
        conn.execute(sql.SQL("COMMENT ON SCHEMA public IS {}").format(sql.Literal(value)))
    finally:
        conn.close()


@pytest.fixture
def marker(disposable_database, db):
    """Sets the public-schema comment of the disposable database; restores it afterwards."""
    conn = db()
    previous, oid = conn.execute(
        "SELECT obj_description('public'::regnamespace, 'pg_namespace'), oid "
        "FROM pg_database WHERE datname = current_database()"
    ).fetchone()

    def apply(value=None):
        expected = seed_demo_data.authorization_marker(disposable_database.name, oid)
        set_public_schema_comment(disposable_database, expected if value is None else value)
        return expected

    apply.oid = oid
    yield apply
    set_public_schema_comment(disposable_database, previous)


@pytest.fixture
def authorized(marker):
    return marker()


def counts(db, schema="public"):
    """Row counts of the seeded tables, schema-qualified so that a test's shadow schema or
    altered search_path cannot redirect the check."""
    conn = db()
    return {
        table: conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, table))
        ).fetchone()[0]
        for table in COUNTS
    }


def run(database, *extra, environ=None, anchor=ANCHOR):
    out = io.StringIO()
    argv = ["--database", database, *extra]
    if anchor is not None:
        argv += ["--anchor", anchor]
    code = seed_demo_data.main(argv, environ=os.environ if environ is None else environ, out=out)
    return code, out.getvalue()


PLACEHOLDER_ENV = {
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "9",
    "DB_USER": "placeholder",
    "DB_PASSWORD": "placeholder-not-a-secret",
}


@pytest.fixture
def no_connection(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the command must refuse before connecting")

    monkeypatch.setattr(seed_demo_data, "open_connection", refuse)


# --- dataset ---------------------------------------------------------------------------


def test_dataset_is_deterministic_synthetic_and_free_of_scheduling_conflicts():
    anchor = datetime(2031, 1, 6, tzinfo=UTC)
    rows = seed_demo_data.appointment_rows(anchor)

    assert rows == seed_demo_data.appointment_rows(anchor)
    assert seed_demo_data.plan(anchor)["appointment_statuses"] == {
        "completed": 6,
        "cancelled": 3,
        "scheduled": 11,
    }
    emails = [p[3] for p in seed_demo_data.PROVIDERS if p[3]]
    assert len(set(emails)) == len(emails)
    assert all(e.endswith("@example.org") for e in emails)
    assert len({p[2] for p in seed_demo_data.PROVIDERS}) == 6
    assert all(start.utcoffset() == timedelta(0) and end > start for _, _, start, end, _, _ in rows)
    scheduled = [
        (prov, start, end) for _, prov, start, end, status, _ in rows if status == "scheduled"
    ]
    for i, (prov, start, end) in enumerate(scheduled):
        for other_prov, other_start, other_end in scheduled[i + 1 :]:
            assert prov != other_prov or end <= other_start or other_end <= start


# --- refusals before connecting ---------------------------------------------------------


# Listed here independently of the module, so that shrinking the module's list fails tests
# instead of silently removing parameters.
EXPECTED_PROTECTED = (
    "medibridge",
    "medibridge_ci_test",
    "medibridge_test",
    "postgres",
    "template0",
    "template1",
)


def test_protected_database_list_is_complete():
    assert set(EXPECTED_PROTECTED) <= seed_demo_data.PROTECTED_DATABASES


@pytest.mark.parametrize("database", EXPECTED_PROTECTED)
def test_every_protected_database_is_refused_before_connecting(database, no_connection, capsys):
    code, output = run(
        database, "--confirm-synthetic-demo", environ={**PLACEHOLDER_ENV, "DB_NAME": database}
    )

    assert code == seed_demo_data.EXIT_REFUSED_TARGET
    assert f"refusing to seed the protected database {database}" in capsys.readouterr().err
    assert "placeholder-not-a-secret" not in output


def test_configured_target_mismatch_is_refused_before_connecting(no_connection, capsys):
    code, _ = run(
        "demo_one", "--confirm-synthetic-demo", environ={**PLACEHOLDER_ENV, "DB_NAME": "demo_two"}
    )

    assert code == seed_demo_data.EXIT_REFUSED_TARGET
    assert "does not match the configured DB_NAME" in capsys.readouterr().err


@pytest.mark.parametrize("missing", ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"])
def test_missing_configuration_is_refused_before_connecting(missing, no_connection, capsys):
    environ = {**PLACEHOLDER_ENV, "DB_NAME": "demo"}
    environ.pop(missing, None)

    code, _ = run("demo", "--confirm-synthetic-demo", environ=environ)

    assert code == seed_demo_data.EXIT_REFUSED_TARGET
    assert f"missing database settings: {missing}" in capsys.readouterr().err


def test_explicit_mode_is_required(disposable_database):
    with pytest.raises(SystemExit) as raised:
        seed_demo_data.main(["--database", disposable_database.name], environ=os.environ)
    assert raised.value.code == 2


@pytest.mark.parametrize(
    ("anchor", "reason"),
    [
        ("next tuesday", "ISO 8601"),
        ("2031-13-40T00:00:00+00:00", "ISO 8601"),
        ("2031-01-06T00:00:00", "UTC offset"),
    ],
    ids=["not-a-date", "impossible-date", "naive"],
)
def test_malformed_or_naive_anchors_are_rejected(anchor, reason, no_connection, capsys):
    with pytest.raises(SystemExit) as raised:
        run("demo", "--dry-run", environ={**PLACEHOLDER_ENV, "DB_NAME": "demo"}, anchor=anchor)

    assert raised.value.code == seed_demo_data.EXIT_INVALID_ARGUMENTS
    assert reason in capsys.readouterr().err


def test_settings_do_not_modify_the_process_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("DB_NAME=from_file\nDB_HOST=file-host\n")
    monkeypatch.delenv("DB_NAME", raising=False)
    monkeypatch.setenv("DB_HOST", "env-host")
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    before = dict(os.environ)

    settings = seed_demo_data.load_settings(env_file=env_file)

    assert (settings["DB_NAME"], settings["DB_HOST"]) == ("from_file", "env-host")
    assert dict(os.environ) == before
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "true")
    assert "DB_NAME" not in seed_demo_data.load_settings(env_file=env_file)


def test_entry_point_runs_main(monkeypatch, capsys):
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["seed_demo_data.py", "--help"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(seed_demo_data.__file__, run_name="__main__")
    assert raised.value.code == 0
    assert "Exit codes" in capsys.readouterr().out


# --- refusals on the real connection ----------------------------------------------------


def test_missing_authorization_is_refused_and_writes_nothing(disposable_database, db, capsys):
    code, _ = run(disposable_database.name, "--confirm-synthetic-demo")

    assert code == seed_demo_data.EXIT_REFUSED_TARGET
    err = capsys.readouterr().err
    assert "not authorized for synthetic demonstration data" in err
    assert "COMMENT ON SCHEMA public IS 'medibridge-synthetic-demo-target:" in err
    assert counts(db) == dict.fromkeys(COUNTS, 0)


@pytest.mark.parametrize(
    "value",
    [
        "medibridge-synthetic-demo-target:another_database:{oid}",
        "medibridge-synthetic-demo-target:{name}:1",
        "medibridge-synthetic-demo-target:{name}:{oid} ",
        "synthetic demo ok",
    ],
    ids=["other-name", "other-oid", "trailing-space", "free-text"],
)
def test_invalid_authorization_is_refused_and_writes_nothing(
    disposable_database, db, marker, capsys, value
):
    marker(value.format(name=disposable_database.name, oid=marker.oid))

    code, _ = run(disposable_database.name, "--confirm-synthetic-demo")
    dry, _ = run(disposable_database.name, "--dry-run")

    assert code == dry == seed_demo_data.EXIT_REFUSED_TARGET
    assert "refusing to seed" in capsys.readouterr().err
    assert counts(db) == dict.fromkeys(COUNTS, 0)


def test_connected_database_mismatch_is_refused(
    disposable_database, db, authorized, monkeypatch, capsys
):
    """The configured, validated name differs from the database actually reached, as with a
    connection-pooler alias. Simulated by connecting to the disposable database."""
    real_open = seed_demo_data.open_connection
    monkeypatch.setattr(
        seed_demo_data,
        "open_connection",
        lambda target, **kw: real_open(
            dataclasses.replace(target, dbname=disposable_database.name), **kw
        ),
    )
    environ = {**os.environ, "DB_NAME": "synthetic_alias"}

    code, _ = run("synthetic_alias", "--confirm-synthetic-demo", environ=environ)

    assert code == seed_demo_data.EXIT_REFUSED_TARGET
    assert f"connected database is {disposable_database.name}" in capsys.readouterr().err
    assert counts(db) == dict.fromkeys(COUNTS, 0)


def test_unreachable_database_is_reported_without_secrets(disposable_database, capsys):
    environ = {**os.environ, "DB_HOST": "127.0.0.1", "DB_PORT": "9"}

    code, output = run(disposable_database.name, "--confirm-synthetic-demo", environ=environ)

    assert code == seed_demo_data.EXIT_DATABASE_UNAVAILABLE
    err = capsys.readouterr().err
    assert "could not connect to 127.0.0.1:9" in err
    for value in (os.environ["DB_PASSWORD"], "password=", "postgresql://"):
        assert value not in output + err


def test_session_is_bounded_and_read_committed(disposable_database):
    target = seed_demo_data.resolve_target(
        disposable_database.name, seed_demo_data.load_settings(os.environ)
    )
    conn = seed_demo_data.open_connection(target, read_only=False)
    try:
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        conn.commit()
        row = conn.execute(
            "SELECT current_setting('lock_timeout'), current_setting('statement_timeout'), "
            "current_setting('application_name'), current_setting('transaction_isolation'), "
            "current_setting('transaction_read_only'), current_setting('search_path')"
        ).fetchone()
        connect_timeout = conn.info.get_parameters().get("connect_timeout")
    finally:
        conn.close()

    # The connection asks for READ COMMITTED explicitly, whatever the session default.
    assert row[:5] == ("10s", "1min", "medibridge-seed-demo-data", "read committed", "off")
    assert [part.strip() for part in row[5].split(",")] == ["pg_catalog", "public", "pg_temp"]
    assert connect_timeout == str(seed_demo_data.CONNECT_TIMEOUT_SECONDS)


def test_isolation_other_than_read_committed_is_refused(
    disposable_database, db, authorized, monkeypatch, capsys
):
    real_open = seed_demo_data.open_connection

    def repeatable_read(target, **kw):
        conn = real_open(target, **kw)
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        return conn

    monkeypatch.setattr(seed_demo_data, "open_connection", repeatable_read)

    code, _ = run(disposable_database.name, "--confirm-synthetic-demo")

    assert code == seed_demo_data.EXIT_REFUSED_TARGET
    assert "transaction isolation is repeatable read" in capsys.readouterr().err
    assert counts(db) == dict.fromkeys(COUNTS, 0)


class FakeIdentityConnection:
    """Returns a fixed identity row (fetchone) and a fixed list of same-named tables in other
    schemas (fetchall); enough for verify_target without a database."""

    def __init__(self, row, port=5432, elsewhere=()):
        self.row, self.elsewhere = row, list(elsewhere)
        self.info = type("Info", (), {"port": port})()

    def execute(self, query, params=None):
        row, elsewhere = self.row, self.elsewhere
        return type(
            "Result",
            (),
            {"fetchone": staticmethod(lambda: row), "fetchall": staticmethod(lambda: elsewhere)},
        )()


DEMO_TARGET = seed_demo_data.Target(
    "db.example.internal", "5432", "medibridge_demo", "operator", "x"
)
DEMO_MARKER = seed_demo_data.authorization_marker("medibridge_demo", 4242)
PINNED_SEARCH_PATH = "pg_catalog, public, pg_temp"  # as PostgreSQL reports the pinned value


def identity_row(
    database="medibridge_demo",
    user="operator",
    session_user="operator",
    oid=4242,
    marker=DEMO_MARKER,
    isolation="read committed",
    read_only="off",
    search_path=PINNED_SEARCH_PATH,
):
    return (database, user, session_user, oid, marker, isolation, read_only, search_path)


@pytest.mark.parametrize(
    ("row", "port", "problem"),
    [
        (identity_row(user="other", session_user="other"), 5432, "connected role is other"),
        (identity_row(session_user="admin"), 5432, "connected role is operator"),
        (identity_row(), 6543, "connected port is 6543"),
        (identity_row(database="postgres", oid=5), 5432, "postgres is protected"),
        (identity_row(read_only="on"), 5432, "transaction_read_only is on, not off"),
        (identity_row(search_path='"$user", public'), 5432, 'search_path is "$user", public'),
    ],
    ids=[
        "role",
        "session-role",
        "port",
        "protected-connected-database",
        "read-only-when-seeding",
        "search-path",
    ],
)
def test_connected_identity_mismatches_are_refused(row, port, problem):
    with pytest.raises(seed_demo_data.SeedRefused) as raised:
        seed_demo_data.verify_target(
            FakeIdentityConnection(row, port), DEMO_TARGET, "medibridge_demo"
        )
    assert raised.value.exit_code == seed_demo_data.EXIT_REFUSED_TARGET
    assert problem in str(raised.value)


def test_dry_run_requires_a_read_only_transaction():
    with pytest.raises(seed_demo_data.SeedRefused) as raised:
        seed_demo_data.verify_target(
            FakeIdentityConnection(identity_row(read_only="off")),
            DEMO_TARGET,
            "medibridge_demo",
            read_only=True,
        )
    assert raised.value.exit_code == seed_demo_data.EXIT_REFUSED_TARGET
    assert "transaction_read_only is off, not on" in str(raised.value)


def test_same_named_tables_in_other_schemas_are_refused():
    conn = FakeIdentityConnection(identity_row(), elsewhere=[("operator", "patients")])
    with pytest.raises(seed_demo_data.SeedRefused) as raised:
        seed_demo_data.verify_target(conn, DEMO_TARGET, "medibridge_demo")
    assert raised.value.exit_code == seed_demo_data.EXIT_REFUSED_TARGET
    assert "outside the public schema (operator.patients)" in str(raised.value)


def test_matching_identity_and_marker_are_accepted():
    for read_only in ("off", "on"):
        identity = seed_demo_data.verify_target(
            FakeIdentityConnection(identity_row(read_only=read_only)),
            DEMO_TARGET,
            "medibridge_demo",
            read_only=read_only == "on",
        )
        assert identity == {"database": "medibridge_demo", "role": "operator", "oid": 4242}


@pytest.mark.parametrize(
    ("marker", "statement"),
    [
        (
            "medibridge-synthetic-demo-target:demo:7",
            "COMMENT ON SCHEMA public IS 'medibridge-synthetic-demo-target:demo:7';",
        ),
        (
            "medibridge-synthetic-demo-target:o'brien_demo:7",
            "COMMENT ON SCHEMA public IS 'medibridge-synthetic-demo-target:o''brien_demo:7';",
        ),
        (
            "medibridge-synthetic-demo-target:a\\'b:7",
            "COMMENT ON SCHEMA public IS E'medibridge-synthetic-demo-target:a\\\\''b:7';",
        ),
    ],
    ids=["plain", "apostrophe", "backslash-and-apostrophe"],
)
def test_authorization_statement_quotes_the_marker(marker, statement):
    assert seed_demo_data.authorization_statement(marker) == statement


def test_refusal_prints_a_quoted_statement_with_control_characters_escaped():
    name = "o'brien\x1b[2J_demo"
    target = dataclasses.replace(DEMO_TARGET, dbname=name)
    conn = FakeIdentityConnection(identity_row(database=name, marker=None))

    with pytest.raises(seed_demo_data.SeedRefused) as raised:
        seed_demo_data.verify_target(conn, target, name)

    message = str(raised.value)
    assert "\x1b" not in message
    assert "o''brien\\x1b[2J_demo:4242" in message
    assert raised.value.exit_code == seed_demo_data.EXIT_REFUSED_TARGET


def test_target_line_escapes_control_characters(monkeypatch, capsys):
    def refuse(target, **kwargs):
        raise seed_demo_data.SeedRefused("stop", seed_demo_data.EXIT_DATABASE_UNAVAILABLE)

    monkeypatch.setattr(seed_demo_data, "open_connection", refuse)
    environ = {**PLACEHOLDER_ENV, "DB_HOST": "db\x1b[2J.example", "DB_NAME": "demo"}

    code, output = run("demo", "--dry-run", environ=environ)

    assert code == seed_demo_data.EXIT_DATABASE_UNAVAILABLE
    assert "\x1b" not in output
    assert "\\x1b[2J" in output


@pytest.mark.parametrize(
    ("exc", "expected", "message"),
    [
        (KeyboardInterrupt(), seed_demo_data.EXIT_UNEXPECTED, "interrupted"),
        (
            psycopg.OperationalError("lost"),
            seed_demo_data.EXIT_DATABASE_UNAVAILABLE,
            "connection failed",
        ),
        (RuntimeError("boom"), seed_demo_data.EXIT_UNEXPECTED, "unexpected error (RuntimeError)"),
        (
            errors.InsufficientPrivilege("permission denied for table hl7_messages"),
            seed_demo_data.EXIT_REFUSED_TARGET,
            "run the command as the role that owns the MediBridge tables",
        ),
    ],
    ids=["interrupt", "connection-lost", "python-error", "insufficient-privilege"],
)
def test_failures_before_commit_are_reported_as_not_committed(exc, expected, message):
    refused = seed_demo_data.failure_before_commit(exc)
    assert refused.exit_code == expected
    assert message in str(refused)
    assert str(refused).endswith("nothing was written (the transaction was not committed)")


def test_default_anchor_is_midnight_utc_today():
    anchor = seed_demo_data.default_anchor()
    now = datetime.now(UTC)
    assert anchor.tzinfo is UTC
    assert (anchor.hour, anchor.minute, anchor.second, anchor.microsecond) == (0, 0, 0, 0)
    assert timedelta(0) <= now - anchor < timedelta(days=1, minutes=1)


# --- dry run ----------------------------------------------------------------------------


def test_dry_run_reports_the_plan_and_writes_nothing(disposable_database, db, authorized):
    code, output = run(disposable_database.name, "--dry-run")

    assert code == seed_demo_data.EXIT_SEEDED
    assert "nothing was written" in output
    assert "advisory only" in output
    assert "'appointments': 20" in output
    assert counts(db) == dict.fromkeys(COUNTS, 0)


@pytest.mark.parametrize("table", ["providers", "hl7_messages", "audit_logs"])
def test_dry_run_and_seed_refuse_existing_records(disposable_database, db, authorized, table):
    statements = {
        "providers": "INSERT INTO providers (first_name, last_name, specialty) "
        "VALUES ('Pre', 'Existing', 'Cardiology')",
        "hl7_messages": "INSERT INTO hl7_messages (sending_application, sending_facility, "
        "message_control_id, message_type) VALUES ('SYN', 'SYN', 'SYN-PRE-1', 'ADT^A04')",
        "audit_logs": "INSERT INTO audit_logs (action, resource_type, outcome) "
        "VALUES ('patient.read', 'patient', 'success')",
    }
    db().execute(statements[table])
    before = counts(db)

    dry, dry_output = run(disposable_database.name, "--dry-run")
    code, _ = run(disposable_database.name, "--confirm-synthetic-demo")

    assert dry == code == seed_demo_data.EXIT_NOT_EMPTY
    assert f"tables not empty: {table}" in dry_output
    assert counts(db) == before


def seed_target(disposable_database):
    return seed_demo_data.resolve_target(
        disposable_database.name, seed_demo_data.load_settings(os.environ)
    )


def test_dry_run_session_is_read_only_and_rejects_writes(disposable_database, db, authorized):
    """The dry run's real session: PostgreSQL reports transaction_read_only = on after the
    target checks, and a write on that same transaction fails with SQLSTATE 25006."""
    conn = seed_demo_data.open_connection(seed_target(disposable_database), read_only=True)
    try:
        seed_demo_data.verify_target(
            conn, seed_target(disposable_database), disposable_database.name, read_only=True
        )
        assert conn.execute("SELECT current_setting('transaction_read_only')").fetchone() == ("on",)
        with pytest.raises(errors.ReadOnlySqlTransaction) as raised:
            conn.execute(
                "INSERT INTO public.providers (first_name, last_name, specialty) "
                "VALUES ('Read', 'Only', 'Cardiology')"
            )
        assert raised.value.sqlstate == "25006"
        conn.rollback()
    finally:
        conn.close()

    assert counts(db) == dict.fromkeys(COUNTS, 0)


def test_dry_run_command_checks_inside_a_read_only_transaction(
    disposable_database, db, authorized, monkeypatch
):
    seen = []
    real_nonempty = seed_demo_data.nonempty_tables

    def recording_nonempty(cur, tables=None):
        seen.append(cur.connection.execute("SHOW transaction_read_only").fetchone()[0])
        return real_nonempty(cur, tables)

    monkeypatch.setattr(seed_demo_data, "nonempty_tables", recording_nonempty)

    code, _ = run(disposable_database.name, "--dry-run")

    assert code == seed_demo_data.EXIT_SEEDED
    assert seen == ["on"]
    assert counts(db) == dict.fromkeys(COUNTS, 0)


@pytest.mark.parametrize("name_part", ["o'brien", "back\\slash"], ids=["apostrophe", "backslash"])
def test_printed_authorization_statement_runs_and_sets_the_exact_marker(
    disposable_database, db, marker, name_part
):
    """The printed statement is valid SQL that stores the value byte for byte (run on the
    disposable database; the marker fixture restores the previous comment)."""
    value = seed_demo_data.authorization_marker(f"synthetic_{name_part}", marker.oid)
    conn = db()
    conn.execute(seed_demo_data.authorization_statement(value))
    stored = conn.execute("SELECT obj_description('public'::regnamespace, 'pg_namespace')")
    assert stored.fetchone()[0] == value


@contextlib.contextmanager
def other_schema(db, name, *, with_table):
    """A schema in the disposable database, dropped again before the test's own cleanup
    (which refuses tables outside public)."""
    conn = db()
    conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        if with_table:
            conn.execute(
                sql.SQL("CREATE TABLE {} (LIKE public.patients INCLUDING ALL)").format(
                    sql.Identifier(name, "patients")
                )
            )
        yield
    finally:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def test_shadow_schema_named_after_the_role_is_refused(disposable_database, db, authorized, capsys):
    """With the default search_path ("$user", public), a schema named after the role would
    shadow public. The command refuses instead of checking one schema and writing another."""
    role = db().execute("SELECT current_user").fetchone()[0]
    with other_schema(db, role, with_table=True):
        dry, _ = run(disposable_database.name, "--dry-run")
        code, _ = run(disposable_database.name, "--confirm-synthetic-demo")
        shadow_rows = (
            db()
            .execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(role, "patients")))
            .fetchone()[0]
        )

    assert dry == code == seed_demo_data.EXIT_REFUSED_TARGET
    assert f"outside the public schema ({role}.patients)" in capsys.readouterr().err
    assert shadow_rows == 0
    assert counts(db) == dict.fromkeys(COUNTS, 0)


def test_altered_database_search_path_cannot_redirect_the_seed(disposable_database, db, authorized):
    """The disposable database's default search_path points elsewhere; the command's session
    still uses the pinned path and writes only to public."""
    database = sql.Identifier(disposable_database.name)
    conn = db()
    with other_schema(db, "synthetic_redirect", with_table=False):
        conn.execute(
            sql.SQL("ALTER DATABASE {} SET search_path = synthetic_redirect").format(database)
        )
        try:
            assert db().execute("SHOW search_path").fetchone()[0] == "synthetic_redirect"
            code, output = run(disposable_database.name, "--confirm-synthetic-demo")
        finally:
            conn.execute(sql.SQL("ALTER DATABASE {} RESET search_path").format(database))

    assert code == seed_demo_data.EXIT_SEEDED, output
    assert counts(db) == COUNTS


def test_seed_runs_as_a_non_superuser_table_owner(
    disposable_database, db, authorized, test_database_config
):
    """
    In the standard test environment (CI's LOGIN CREATEDB role) the command's own session is
    not a superuser, owns the database and every checked table, and still seeds; the marker
    was set by that same role (the `authorized` fixture). Skipped only under the explicit
    local TEST_DB_ALLOW_SUPERUSER=1 override.
    """
    conn = seed_demo_data.open_connection(seed_target(disposable_database), read_only=False)
    try:
        is_superuser, owns_database, not_owned = conn.execute(
            "SELECT r.rolsuper, d.datdba = r.oid, "
            "ARRAY(SELECT c.relname FROM pg_class c "
            "      WHERE c.relnamespace = 'public'::regnamespace AND c.relname = ANY(%s) "
            "      AND c.relowner <> r.oid ORDER BY 1) "
            "FROM pg_roles r, pg_database d "
            "WHERE r.rolname = current_user AND d.datname = current_database()",
            (list(seed_demo_data.REQUIRED_EMPTY_TABLES),),
        ).fetchone()
        conn.rollback()
    finally:
        conn.close()

    if is_superuser:
        assert test_database_config.allow_superuser, "superuser without the explicit override"
        pytest.skip("running as a superuser under the explicit TEST_DB_ALLOW_SUPERUSER override")
    assert owns_database
    assert not_owned == []

    code, output = run(disposable_database.name, "--confirm-synthetic-demo")

    assert code == seed_demo_data.EXIT_SEEDED, output
    assert counts(db) == COUNTS


# --- real seeding ------------------------------------------------------------------------


def test_seed_commits_the_dataset_and_existing_apis_return_it(disposable_database, db, authorized):
    db()
    admin = create_user(
        username="synthetic.seed.admin",
        email="seed.admin@example.org",
        password=secrets.token_urlsafe(24),
        role="admin",
    )
    user_before = db().execute("SELECT * FROM users").fetchall()

    code, output = run(disposable_database.name, "--confirm-synthetic-demo")

    assert code == seed_demo_data.EXIT_SEEDED, output
    assert "transaction committed" in output
    assert os.environ["DB_PASSWORD"] not in output
    assert counts(db) == COUNTS
    conn = db()
    assert conn.execute("SELECT * FROM users").fetchall() == user_before  # untouched
    assert conn.execute("SELECT count(*) FROM audit_logs").fetchone()[0] == 0
    orphans = conn.execute(
        "SELECT count(*) FROM appointments a LEFT JOIN patients p ON p.id = a.patient_id "
        "LEFT JOIN providers r ON r.id = a.provider_id WHERE p.id IS NULL OR r.id IS NULL"
    ).fetchone()[0]
    assert orphans == 0
    first = conn.execute("SELECT min(starts_at) FROM appointments").fetchone()[0]
    assert first == datetime(2030, 12, 27, 14, tzinfo=UTC)  # anchor - 10 days, 14:00 UTC

    previous = dict(app_main.app.dependency_overrides)
    app_main.app.dependency_overrides[get_current_user] = lambda: {
        k: admin[k] for k in ("id", "username", "email", "role", "is_active")
    }
    try:
        client = TestClient(app_main.app)
        assert len(client.get("/patients").json()) == 16
        assert len(client.get("/providers").json()) == 6
        assert len(client.get("/appointments", params={"status": "scheduled"}).json()) == 11
        bundle = client.get("/fhir/Patient", params={"identifier": "DEMO-000001"}).json()
    finally:
        app_main.app.dependency_overrides.clear()
        app_main.app.dependency_overrides.update(previous)
    assert bundle["total"] == 1
    assert bundle["entry"][0]["resource"]["name"][0]["family"] == "Demo"


def test_offset_aware_anchor_is_honoured(disposable_database, db, authorized):
    code, _ = run(
        disposable_database.name, "--confirm-synthetic-demo", anchor="2031-01-06T00:00:00-06:00"
    )

    assert code == seed_demo_data.EXIT_SEEDED
    first = db().execute("SELECT min(starts_at) FROM appointments").fetchone()[0]
    assert first == datetime(2030, 12, 27, 20, tzinfo=UTC)  # 14:00 at -06:00 is 20:00 UTC


def test_repeat_run_is_refused_and_changes_nothing(disposable_database, db, authorized):
    assert run(disposable_database.name, "--confirm-synthetic-demo")[0] == 0
    before = db().execute("SELECT max(id) FROM appointments").fetchone()[0], counts(db)

    code, _ = run(disposable_database.name, "--confirm-synthetic-demo")
    dry, _ = run(disposable_database.name, "--dry-run")

    assert code == dry == seed_demo_data.EXIT_NOT_EMPTY
    assert (db().execute("SELECT max(id) FROM appointments").fetchone()[0], counts(db)) == before


# --- failures and outcome reporting ------------------------------------------------------


def test_failure_midway_rolls_back_and_is_sanitized(
    disposable_database, db, authorized, monkeypatch, capsys
):
    clash = (0, 0, 2, 14, 30, "scheduled", "Overlaps an earlier scheduled slot")
    monkeypatch.setattr(seed_demo_data, "APPOINTMENTS", (*seed_demo_data.APPOINTMENTS, clash))

    code, output = run(disposable_database.name, "--confirm-synthetic-demo")

    assert code == seed_demo_data.EXIT_UNEXPECTED
    err = capsys.readouterr().err
    assert "unexpected database error (SQLSTATE 23P01)" in err
    assert "nothing was written (the transaction was not committed)" in err
    assert "appointments_no_provider_overlap" not in err  # driver detail is not echoed
    assert os.environ["DB_PASSWORD"] not in output + err
    assert counts(db) == dict.fromkeys(COUNTS, 0)


def test_missing_schema_is_reported(disposable_database, db, authorized, monkeypatch, capsys):
    monkeypatch.setattr(
        seed_demo_data,
        "REQUIRED_EMPTY_TABLES",
        (*seed_demo_data.REQUIRED_EMPTY_TABLES, "synthetic_missing_table"),
    )

    code, _ = run(disposable_database.name, "--confirm-synthetic-demo")

    assert code == seed_demo_data.EXIT_DATABASE_UNAVAILABLE
    assert "run `python migrate.py` first" in capsys.readouterr().err
    assert counts(db) == dict.fromkeys(COUNTS, 0)


def test_lock_timeout_reports_and_writes_nothing(
    disposable_database, db, authorized, monkeypatch, capsys
):
    """Another session holds a write lock on providers; the command gives up after its
    (shortened) lock timeout. The holder is bounded server-side as well."""
    monkeypatch.setattr(seed_demo_data, "LOCK_TIMEOUT", "1s")
    holder = verified_connection(disposable_database, autocommit=False)
    try:
        holder.execute(f"SET idle_in_transaction_session_timeout = '{HOLDER_IDLE_LIMIT}'")
        holder.commit()
        holder.execute("LOCK TABLE providers IN ROW EXCLUSIVE MODE")
        started = time.monotonic()
        code, _ = run(disposable_database.name, "--confirm-synthetic-demo")
        waited = time.monotonic() - started
    finally:
        with contextlib.suppress(psycopg.OperationalError):
            holder.rollback()
        holder.close()

    assert code == seed_demo_data.EXIT_DATABASE_UNAVAILABLE
    err = capsys.readouterr().err
    assert "another session is using the tables" in err
    assert "not committed" in err
    assert waited < 10
    assert counts(db) == dict.fromkeys(COUNTS, 0)


class CommitFails:
    """Wraps a real connection; COMMIT fails as described by `mode`."""

    def __init__(self, conn, mode):
        self._conn, self._mode = conn, mode

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        if self._mode == "connection-lost":
            self._conn.close()  # the server rolls back; the client cannot know
            raise psycopg.OperationalError("connection lost")
        raise errors.SerializationFailure("rejected")


@pytest.mark.parametrize(
    ("mode", "expected", "message"),
    [
        (
            "connection-lost",
            seed_demo_data.EXIT_OUTCOME_UNKNOWN,
            "outcome of the commit is unknown",
        ),
        ("server-rejected", seed_demo_data.EXIT_UNEXPECTED, "rejected the commit (SQLSTATE 40001)"),
    ],
)
def test_commit_outcome_is_reported_honestly(
    disposable_database, db, authorized, monkeypatch, capsys, mode, expected, message
):
    real_open = seed_demo_data.open_connection
    monkeypatch.setattr(
        seed_demo_data, "open_connection", lambda t, **kw: CommitFails(real_open(t, **kw), mode)
    )

    code, _ = run(disposable_database.name, "--confirm-synthetic-demo")

    assert code == expected
    err = capsys.readouterr().err
    assert message in err
    if expected == seed_demo_data.EXIT_OUTCOME_UNKNOWN:
        assert "Do not run the command again" in err
        assert "nothing was written" not in err
    assert counts(db) == dict.fromkeys(COUNTS, 0)  # in these simulations nothing committed


# --- concurrent initializer ---------------------------------------------------------------


# A must outlive B's whole wait (connect timeout + lock timeout), so that A's locks can only
# be released by this test, never by the server while B is still waiting.
RACE_HOLDER_IDLE_LIMIT = "45s"
RACE_JOIN_SECONDS = 90


class ControlledFailure(Exception):
    """Raised by the failure probe at the moment B is waiting for A's locks."""


def race_initializers(disposable_database, db, monkeypatch, *, observe_seconds, fail_while_waiting):
    """
    A seeds inside an open transaction (holding the table locks). B is the real command in
    a worker thread. B is observed (pg_blocking_pids) waiting for A on LOCK TABLE; A then
    commits and B, reading under READ COMMITTED after the lock, finds the rows and refuses.

    Failure path: if anything fails before A commits, B is joined first, while A still holds
    its locks, so B can only give up at its lock timeout and write nothing. A is rolled back
    only afterwards. (Rolling A back first would let a still-waiting B seed the database.)

    Bounds: B has the command's own lock and statement timeouts; A's session is ended by the
    server after RACE_HOLDER_IDLE_LIMIT idle; the worker is joined for at most
    RACE_JOIN_SECONDS at a time.
    """
    opened = queue.Queue()
    real_open = seed_demo_data.open_connection

    def recording_open(target, **kw):
        conn = real_open(target, **kw)
        opened.put(conn.info.backend_pid)
        return conn

    monkeypatch.setattr(seed_demo_data, "open_connection", recording_open)
    results = queue.Queue()
    a = verified_connection(disposable_database, autocommit=False)
    observer = db()
    thread = threading.Thread(
        target=lambda: results.put(run(disposable_database.name, "--confirm-synthetic-demo")),
        name="seed-race-worker",
    )
    evidence = {}
    a_committed = False
    try:
        a.execute(f"SET idle_in_transaction_session_timeout = '{RACE_HOLDER_IDLE_LIMIT}'")
        a.commit()
        seed_demo_data.seed_in_transaction(a, datetime(2031, 1, 6, tzinfo=UTC))
        thread.start()
        b_pid = opened.get(timeout=15)
        deadline = time.monotonic() + observe_seconds
        while True:
            row = observer.execute(
                "SELECT pg_blocking_pids(pid), wait_event_type, query FROM pg_stat_activity "
                "WHERE pid = %s",
                (b_pid,),
            ).fetchone()
            if row and a.info.backend_pid in row[0] and row[1] == "Lock":
                evidence = {"blocked_by": row[0], "query": row[2]}
                break
            assert time.monotonic() < deadline, f"B was not observed waiting for A: {row}"
            time.sleep(0.05)
        if fail_while_waiting:
            raise ControlledFailure("failure while B waits for A's locks")
        a.commit()
        a_committed = True
    finally:
        if not a_committed and thread.ident is not None:
            thread.join(RACE_JOIN_SECONDS)  # A still holds the locks: B can only time out
        with contextlib.suppress(psycopg.Error):
            a.rollback()
        a.close()
        if thread.ident is not None:
            thread.join(RACE_JOIN_SECONDS)
        assert not thread.is_alive(), f"worker still running after {RACE_JOIN_SECONDS} s"
    return results.get_nowait(), evidence


def test_concurrent_initializer_waits_and_then_refuses(
    disposable_database, db, authorized, monkeypatch
):
    (code, _), evidence = race_initializers(
        disposable_database,
        db,
        monkeypatch,
        observe_seconds=8,  # well inside B's 10 s lock timeout
        fail_while_waiting=False,
    )

    assert evidence["query"].upper().startswith("LOCK TABLE")
    assert code == seed_demo_data.EXIT_NOT_EMPTY
    assert counts(db) == COUNTS


def test_failure_while_initializer_waits_lets_it_commit_nothing(
    disposable_database, db, authorized, monkeypatch, capsys
):
    """Controlled failure probe of the race's failure path: the test fails while B is
    waiting. B must then time out against A's still-held locks, and A must roll back, so
    the disposable database stays empty."""
    monkeypatch.setattr(seed_demo_data, "LOCK_TIMEOUT", "3s")
    with pytest.raises(ControlledFailure):
        race_initializers(
            disposable_database, db, monkeypatch, observe_seconds=2.5, fail_while_waiting=True
        )

    assert "another session is using the tables" in capsys.readouterr().err
    assert counts(db) == dict.fromkeys(COUNTS, 0)
