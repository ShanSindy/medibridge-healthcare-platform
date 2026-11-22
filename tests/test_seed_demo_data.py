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


def counts(db):
    conn = db()
    return {
        table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608 - fixed names
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
            "current_setting('application_name'), current_setting('transaction_isolation')"
        ).fetchone()
        connect_timeout = conn.info.get_parameters().get("connect_timeout")
    finally:
        conn.close()

    # The connection asks for READ COMMITTED explicitly, whatever the session default.
    assert row == ("10s", "1min", "medibridge-seed-demo-data", "read committed")  # 60s
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


def test_concurrent_initializer_waits_and_then_refuses(
    disposable_database, db, authorized, monkeypatch
):
    """
    A seeds inside an open transaction (holding the table locks). B is the real command in
    a worker thread. B is observed (pg_blocking_pids) waiting for A on LOCK TABLE; A then
    commits and B, reading under READ COMMITTED after the lock, finds the rows and refuses.

    Bounds: B has the command's own 10 s lock and 60 s statement timeouts; A's session is
    ended by the server after HOLDER_IDLE_LIMIT idle; the worker is joined for at most 90 s.
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
    try:
        a.execute(f"SET idle_in_transaction_session_timeout = '{HOLDER_IDLE_LIMIT}'")
        a.commit()
        seed_demo_data.seed_in_transaction(a, datetime(2031, 1, 6, tzinfo=UTC))
        thread.start()
        b_pid = opened.get(timeout=15)
        deadline = time.monotonic() + 8  # well inside B's 10 s lock timeout
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
        a.commit()
    finally:
        with contextlib.suppress(psycopg.Error):
            a.rollback()
        a.close()
        if thread.ident is not None:
            thread.join(90)
    assert not thread.is_alive(), "worker still running after 90 s"

    code, _ = results.get_nowait()
    assert evidence["query"].upper().startswith("LOCK TABLE")
    assert code == seed_demo_data.EXIT_NOT_EMPTY
    assert counts(db) == COUNTS
