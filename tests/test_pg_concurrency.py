"""
Deterministic concurrency tests against real PostgreSQL.

Each scenario uses three connections to this session's disposable database:

- A: a direct, identity-checked connection that holds an uncommitted write;
- B: the application's own connection, opened by the real service code
  (`appointment_service` or `hl7_service`) in a worker thread;
- an autocommit observer that waits until PostgreSQL reports that B is blocked
  by A (`pg_blocking_pids`), before A is committed or rolled back.

Observation only: the service module's `get_connection` is wrapped so that the
test can verify the connection's database identity and record its backend PID
and lock settings. The wrapper returns the real connection; SQL, transactions
and constraints are untouched.

Bounds. Every connection opened during these tests, including B's, gets
`lock_timeout` and `statement_timeout` from PGOPTIONS (set only for the test).
The observer waits at most OBSERVE_SECONDS for the blocking relationship, which
leaves LOCK_TIMEOUT_MS - OBSERVE_SECONDS of margin to release A before B would
give up on its own. The worker is joined for at most JOIN_SECONDS, which is
longer than B's statement timeout. If the worker cannot be shown to have
finished, the test run stops rather than continue into database cleanup.
"""

import os
import queue
import sys
import threading
import time

import psycopg
import pytest
from psycopg import errors

import appointment_service
import hl7_service
from models import AppointmentCreate
from tests import db_safety

CONNECT_TIMEOUT_SECONDS = 10
LOCK_TIMEOUT_MS = 30_000
STATEMENT_TIMEOUT_MS = 45_000
OBSERVE_SECONDS = 15
JOIN_SECONDS = 60
CONTAINMENT_SECONDS = 15
POLL_SECONDS = 0.05


# --- infrastructure --------------------------------------------------------------------


@pytest.fixture
def bounded_connections(monkeypatch):
    """Applies connect, lock and statement timeouts to every libpq connection in the test."""
    monkeypatch.setenv("PGCONNECT_TIMEOUT", str(CONNECT_TIMEOUT_SECONDS))
    monkeypatch.setenv(
        "PGOPTIONS",
        f"-c lock_timeout={LOCK_TIMEOUT_MS} -c statement_timeout={STATEMENT_TIMEOUT_MS}",
    )


@pytest.fixture
def connect(disposable_database, bounded_connections):
    """Identity-checked connections to this session's disposable database, all closed after."""
    opened = []

    def open_connection(autocommit):
        conn = psycopg.connect(
            **disposable_database.config.connect_kwargs(disposable_database.name),
            autocommit=autocommit,
        )
        opened.append(conn)
        verify_identity(conn, disposable_database)
        if not autocommit:
            conn.commit()  # end the identity check's transaction; A starts clean
        return conn

    yield open_connection
    for conn in opened:
        if not conn.closed:
            if not conn.autocommit:
                conn.rollback()
            conn.close()


def verify_identity(conn, disposable_database):
    assert conn.info.dbname == disposable_database.name
    found, role_oid = db_safety.fetch_identity(conn, disposable_database.name)
    db_safety.check_identity(
        session_name=disposable_database.name,
        created=disposable_database.created,
        found=found,
        current_role_oid=role_oid,
        require_marker=True,
    )


def observe_worker_connection(monkeypatch, module, disposable_database):
    """Wraps `module.get_connection`; returns a dict filled in when the worker connects."""
    real_get_connection = module.get_connection
    seen = {"connected": threading.Event(), "stopped": threading.Event()}

    def observed_get_connection():
        if seen["stopped"].is_set():
            raise RuntimeError("worker containment is active; no new connections")
        conn = real_get_connection()
        try:
            verify_identity(conn, disposable_database)
            seen["pid"], seen["backend_start"], seen["role"] = conn.execute(
                "SELECT pid, backend_start, usename FROM pg_stat_activity "
                "WHERE pid = pg_backend_pid()"
            ).fetchone()
            seen["lock_timeout"] = conn.execute("SHOW lock_timeout").fetchone()[0]
            seen["statement_timeout"] = conn.execute("SHOW statement_timeout").fetchone()[0]
            conn.rollback()  # leave the real service code a fresh transaction
        except BaseException:
            conn.close()
            raise
        seen["connected"].set()
        return conn

    monkeypatch.setattr(module, "get_connection", observed_get_connection)
    return seen


def run_race(connect, seen, hold, operation, finish, disposable_database):
    """
    A runs `hold(cursor)` without committing. B runs `operation()` in a worker thread.
    Waits until PostgreSQL reports A blocking B, then commits or rolls back A and
    collects B's outcome. Returns (outcome, evidence); outcome is ("ok", value) or
    ("error", exception).
    """
    a = connect(autocommit=False)
    observer = connect(autocommit=True)
    a_pid = a.info.backend_pid
    results = queue.Queue()

    def worker():
        try:
            results.put(("ok", operation()))
        except BaseException as exc:  # every outcome, including failures, goes to the test
            results.put(("error", exc))

    thread = threading.Thread(target=worker, name="pg-race-worker")
    evidence = {"a_pid": a_pid}
    try:
        with a.cursor() as cursor:
            hold(cursor)
        thread.start()

        deadline = time.monotonic() + OBSERVE_SECONDS
        assert seen["connected"].wait(timeout=OBSERVE_SECONDS), "worker never connected"
        b_pid = seen["pid"]
        evidence.update(
            b_pid=b_pid,
            b_lock_timeout=seen["lock_timeout"],
            b_statement_timeout=seen["statement_timeout"],
        )
        assert b_pid != a_pid
        while True:
            row = observer.execute(
                "SELECT pg_blocking_pids(pid), wait_event_type, datname, query "
                "FROM pg_stat_activity WHERE pid = %s",
                (b_pid,),
            ).fetchone()
            if row is not None and a_pid in row[0] and row[1] == "Lock":
                evidence.update(blocked_by=row[0], database=row[2], b_query=row[3])
                break
            if not thread.is_alive():
                pytest.fail(f"worker finished before it was blocked by A: {results.queue}")
            if time.monotonic() > deadline:
                pytest.fail(f"B was not observed blocked by A within {OBSERVE_SECONDS}s: {row}")
            time.sleep(POLL_SECONDS)  # polling interval only; never a correctness condition

        a.commit() if finish == "commit" else a.rollback()
    finally:
        # Each step runs even if an earlier one raises, so A is always closed, the
        # worker is always contained and the observer is always closed.
        try:
            if not a.closed:
                try:
                    a.rollback()
                finally:
                    a.close()
        finally:
            try:
                contain_worker(thread, seen, disposable_database)
            finally:
                observer.close()

    outcome = results.get_nowait()
    evidence["b_closed"] = wait_until_backend_gone(connect, evidence.get("b_pid"))
    evidence["database_matches"] = evidence.get("database") == disposable_database.name
    return outcome, evidence


def contain_worker(thread, seen, disposable_database):
    """
    Joins the worker. If it outlives JOIN_SECONDS, blocks new worker connections,
    terminates B's own backend (identity verified) and joins again. If the thread
    still runs, or B's backend cannot be identified and terminated, the process exits
    at once: no fixture teardown may run while a live worker could still write.
    """
    if thread.ident is None:
        return
    thread.join(JOIN_SECONDS)
    if not thread.is_alive():
        return
    seen["stopped"].set()
    try:
        terminate_worker_backend(seen, disposable_database)
        thread.join(CONTAINMENT_SECONDS)
        problem = "the worker thread still runs after its backend was terminated"
    except Exception as exc:  # any failure to contain B ends the run below
        problem = f"B's backend could not be terminated safely: {exc}"
    if thread.is_alive():
        sys.stderr.write(f"\nSTOPPING: {problem}; exiting without fixture teardown.\n")
        sys.stderr.flush()
        os._exit(3)
    pytest.fail(f"worker exceeded {JOIN_SECONDS}s; its backend was terminated and it stopped")


class WorkerContainmentError(RuntimeError):
    pass


def terminate_worker_backend(seen, disposable_database):
    """
    Terminates only B's backend: the same PID, start time, database and role, from a
    connection verified to be this session's disposable database; then checks it is gone.
    """
    pid, started, role = seen.get("pid"), seen.get("backend_start"), seen.get("role")
    if None in (pid, started, role):
        raise WorkerContainmentError("B's backend identity was never recorded")
    conn = psycopg.connect(
        **disposable_database.config.connect_kwargs(disposable_database.name), autocommit=True
    )
    try:
        verify_identity(conn, disposable_database)
        row = conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE pid = %s AND backend_start = %s AND datname = %s "
            "AND usename = %s AND usename = current_user",
            (pid, started, disposable_database.name, role),
        ).fetchone()
        if row is None or row[0] is not True:
            raise WorkerContainmentError(f"backend {pid} did not match B's identity")
        deadline = time.monotonic() + CONTAINMENT_SECONDS
        while conn.execute("SELECT 1 FROM pg_stat_activity WHERE pid = %s", (pid,)).fetchone():
            if time.monotonic() > deadline:
                raise WorkerContainmentError(f"backend {pid} still present after termination")
            time.sleep(POLL_SECONDS)
    finally:
        conn.close()


def wait_until_backend_gone(connect, pid):
    if pid is None:
        return False
    observer = connect(autocommit=True)
    deadline = time.monotonic() + OBSERVE_SECONDS
    try:
        while time.monotonic() < deadline:
            row = observer.execute("SELECT 1 FROM pg_stat_activity WHERE pid = %s", (pid,))
            if row.fetchone() is None:
                return True
            time.sleep(POLL_SECONDS)
        return False
    finally:
        observer.close()


def assert_blocking_evidence(evidence, statement_prefix, record_property):
    record_property(
        "blocking_evidence",
        {
            k: evidence.get(k)
            for k in (
                "a_pid",
                "b_pid",
                "blocked_by",
                "b_lock_timeout",
                "b_statement_timeout",
                "b_closed",
            )
        },
    )
    assert evidence["a_pid"] in evidence["blocked_by"]
    assert evidence["database_matches"]
    assert evidence["b_query"].lstrip().upper().startswith(statement_prefix)
    assert evidence["b_lock_timeout"] == "30s"
    assert evidence["b_statement_timeout"] == "45s"
    assert evidence["b_closed"]


# --- appointment overlap ---------------------------------------------------------------


@pytest.fixture
def schedule(connect):
    conn = connect(autocommit=True)
    patient_id = conn.execute(
        "INSERT INTO patients (first_name, last_name, date_of_birth, gender) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        ("Riley", "Concurrent", "1979-08-21", "unknown"),
    ).fetchone()[0]
    provider_id = conn.execute(
        "INSERT INTO providers (first_name, last_name, specialty) VALUES (%s, %s, %s) RETURNING id",
        ("Casey", "Contention", "Neurology"),
    ).fetchone()[0]
    return patient_id, provider_id


def hold_appointment(patient_id, provider_id):
    def hold(cursor):
        cursor.execute(
            "INSERT INTO appointments (patient_id, provider_id, starts_at, ends_at, reason) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                patient_id,
                provider_id,
                "2031-04-01T09:00:00-06:00",
                "2031-04-01T10:00:00-06:00",
                "held by transaction A",
            ),
        )

    return hold


def book_overlapping(patient_id, provider_id):
    return lambda: appointment_service.create_appointment(
        AppointmentCreate(
            patient_id=patient_id,
            provider_id=provider_id,
            starts_at="2031-04-01T09:30:00-06:00",
            ends_at="2031-04-01T10:30:00-06:00",
            reason="attempted by worker B",
        )
    )


def committed_reasons(connect, provider_id):
    conn = connect(autocommit=True)
    rows = conn.execute(
        "SELECT reason FROM appointments WHERE provider_id = %s ORDER BY id", (provider_id,)
    )
    return [r[0] for r in rows.fetchall()]


def test_overlapping_booking_waits_for_and_fails_after_a_commits(
    connect, schedule, monkeypatch, disposable_database, record_property
):
    patient_id, provider_id = schedule
    seen = observe_worker_connection(monkeypatch, appointment_service, disposable_database)

    (kind, value), evidence = run_race(
        connect,
        seen,
        hold_appointment(patient_id, provider_id),
        book_overlapping(patient_id, provider_id),
        "commit",
        disposable_database,
    )

    assert_blocking_evidence(evidence, "INSERT INTO APPOINTMENTS", record_property)
    assert kind == "error"
    assert isinstance(value, errors.ExclusionViolation)
    assert value.sqlstate == "23P01"
    assert value.diag.constraint_name == "appointments_no_provider_overlap"
    assert committed_reasons(connect, provider_id) == ["held by transaction A"]


def test_overlapping_booking_waits_for_and_succeeds_after_a_rolls_back(
    connect, schedule, monkeypatch, disposable_database, record_property
):
    patient_id, provider_id = schedule
    seen = observe_worker_connection(monkeypatch, appointment_service, disposable_database)

    (kind, value), evidence = run_race(
        connect,
        seen,
        hold_appointment(patient_id, provider_id),
        book_overlapping(patient_id, provider_id),
        "rollback",
        disposable_database,
    )

    assert_blocking_evidence(evidence, "INSERT INTO APPOINTMENTS", record_property)
    assert kind == "ok", value
    assert value["status"] == "scheduled"
    assert committed_reasons(connect, provider_id) == ["attempted by worker B"]


# --- HL7 duplicate control ID ------------------------------------------------------------

MRN_SYSTEM = "https://medibridge.example.org/mrn"
MESSAGE = (
    "MSH|^~\\&|SYN_CONC|SYN_FAC|MEDIBRIDGE|MEDIBRIDGE|20311106120000||ADT^A04|SYN-CONC-0001|P|2.5\r"
    "EVN|A04|20311106120000\r"
    "PID|1||SYN-CONC-123^^^SYN_FAC^MR||Concurrent^Quinn||19900101|U"
)


def test_duplicate_hl7_control_id_waits_and_is_reported_as_duplicate(
    connect, monkeypatch, disposable_database, record_property
):
    """
    A uses direct SQL to hold the same writes the first message would make (tracking row,
    patient, MRN identifier) without committing. B is the real `register_patient_from_hl7`.
    """
    seen = observe_worker_connection(monkeypatch, hl7_service, disposable_database)

    def hold(cursor):
        cursor.execute(
            "INSERT INTO hl7_messages "
            "(sending_application, sending_facility, message_control_id, message_type) "
            "VALUES (%s, %s, %s, %s)",
            ("SYN_CONC", "SYN_FAC", "SYN-CONC-0001", "ADT^A04"),
        )
        cursor.execute(
            "INSERT INTO patients (first_name, last_name, date_of_birth, gender) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            ("Quinn", "Concurrent", "1990-01-01", "unknown"),
        )
        patient_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO patient_identifiers (patient_id, system, value) VALUES (%s, %s, %s)",
            (patient_id, MRN_SYSTEM, "SYN-CONC-123"),
        )

    (kind, value), evidence = run_race(
        connect,
        seen,
        hold,
        lambda: hl7_service.register_patient_from_hl7(MESSAGE),
        "commit",
        disposable_database,
    )

    assert_blocking_evidence(evidence, "INSERT INTO HL7_MESSAGES", record_property)
    assert kind == "ok", value
    assert value == {
        "status": "duplicate",
        "mrn": "SYN-CONC-123",
        "message_control_id": "SYN-CONC-0001",
    }
    conn = connect(autocommit=True)
    counts = conn.execute(
        "SELECT (SELECT count(*) FROM hl7_messages), (SELECT count(*) FROM patients), "
        "(SELECT count(*) FROM patient_identifiers WHERE value = %s)",
        ("SYN-CONC-123",),
    ).fetchone()
    assert counts == (1, 1, 1)
