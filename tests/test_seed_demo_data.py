"""
seed_demo_data.py against this session's disposable database: dataset consistency, target
refusals, committed records visible through the existing APIs, repeat refusal, dry run,
rollback after a mid-way failure and a concurrent initializer.
"""

import io
import os
import secrets
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

import main as app_main
import seed_demo_data
from auth_dependencies import get_current_user
from tests import db_safety
from tests.test_pg_concurrency import (  # noqa: F401 - fixtures used by the race test
    assert_blocking_evidence,
    bounded_connections,
    connect,
    observe_worker_connection,
    run_race,
)
from user_service import create_user

ANCHOR = "2031-01-06T00:00:00+00:00"
COUNTS = {"patients": 16, "patient_identifiers": 16, "providers": 6, "appointments": 20}


@pytest.fixture
def db(disposable_database):
    opened = []

    def open_verified():
        conn = psycopg.connect(
            **disposable_database.config.connect_kwargs(disposable_database.name),
            autocommit=True,
        )
        opened.append(conn)
        found, role_oid = db_safety.fetch_identity(conn, disposable_database.name)
        db_safety.check_identity(
            session_name=disposable_database.name,
            created=disposable_database.created,
            found=found,
            current_role_oid=role_oid,
            require_marker=True,
        )
        return conn

    yield open_verified
    for conn in opened:
        conn.close()


def counts(db):
    conn = db()
    return {
        table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608 - fixed names
        for table in COUNTS
    }


def run(database, *extra, environ=None):
    out = io.StringIO()
    code = seed_demo_data.main(
        ["--database", database, *extra, "--anchor", ANCHOR],
        environ=os.environ if environ is None else environ,
        out=out,
    )
    return code, out.getvalue()


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


# --- target guard (no writes) -----------------------------------------------------------


@pytest.mark.parametrize(
    ("database", "settings_name"),
    [("medibridge", "medibridge"), ("medibridge_test", "medibridge_test"), ("other_db", "x")],
    ids=["protected-dev", "protected-test", "mismatch"],
)
def test_unsafe_targets_are_refused_before_connecting(database, settings_name):
    env = {
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "9",
        "DB_NAME": settings_name,
        "DB_USER": "placeholder",
        "DB_PASSWORD": "placeholder-not-a-secret",
    }
    code, output = run(database, "--confirm-synthetic-demo", environ=env)

    assert code == seed_demo_data.EXIT_REFUSED_TARGET
    assert "placeholder-not-a-secret" not in output


def test_explicit_mode_is_required(disposable_database):
    with pytest.raises(SystemExit) as raised:
        seed_demo_data.main(["--database", disposable_database.name], environ=os.environ)
    assert raised.value.code == 2


def test_dry_run_reports_the_plan_and_writes_nothing(disposable_database, db):
    code, output = run(disposable_database.name, "--dry-run")

    assert code == seed_demo_data.EXIT_SEEDED
    assert "nothing was written" in output
    assert "'appointments': 20" in output
    assert counts(db) == dict.fromkeys(COUNTS, 0)


# --- real seeding ------------------------------------------------------------------------


def test_seed_commits_the_dataset_and_existing_apis_return_it(disposable_database, db):
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


def test_repeat_run_is_refused_and_changes_nothing(disposable_database, db):
    assert run(disposable_database.name, "--confirm-synthetic-demo")[0] == 0
    before = db().execute("SELECT max(id) FROM appointments").fetchone()[0], counts(db)

    code, _ = run(disposable_database.name, "--confirm-synthetic-demo")
    dry, _ = run(disposable_database.name, "--dry-run")

    assert code == dry == seed_demo_data.EXIT_NOT_EMPTY
    assert (db().execute("SELECT max(id) FROM appointments").fetchone()[0], counts(db)) == before


def test_failure_midway_rolls_back_every_insert(disposable_database, db, monkeypatch):
    clash = (0, 0, 2, 14, 30, "scheduled", "Overlaps an earlier scheduled slot")
    monkeypatch.setattr(seed_demo_data, "APPOINTMENTS", (*seed_demo_data.APPOINTMENTS, clash))

    code, _ = run(disposable_database.name, "--confirm-synthetic-demo")

    assert code == seed_demo_data.EXIT_UNEXPECTED
    assert counts(db) == dict.fromkeys(COUNTS, 0)


def test_concurrent_initializer_waits_and_then_refuses(
    connect,  # noqa: F811 - the imported pytest fixture
    monkeypatch,
    disposable_database,
    record_property,
):
    """A seeds inside an open transaction (holding the lock); B is the real CLI."""
    seen = observe_worker_connection(monkeypatch, seed_demo_data, disposable_database)
    anchor = datetime(2031, 1, 6, tzinfo=UTC)

    (kind, value), evidence = run_race(
        connect,
        seen,
        lambda cursor: seed_demo_data.seed_in_transaction(cursor.connection, anchor),
        lambda: run(disposable_database.name, "--confirm-synthetic-demo")[0],
        "commit",
        disposable_database,
    )

    assert_blocking_evidence(evidence, "LOCK TABLE", record_property)
    assert (kind, value) == ("ok", seed_demo_data.EXIT_NOT_EMPTY)
    conn = connect(autocommit=True)
    assert conn.execute("SELECT count(*) FROM appointments").fetchone()[0] == 20
    assert conn.execute("SELECT count(*) FROM patients").fetchone()[0] == 16
