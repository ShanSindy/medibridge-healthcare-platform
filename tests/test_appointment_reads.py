"""
Appointment reads (get_all_appointments, get_appointment_by_id) against this session's
disposable database.

The contract tests use fixed expected results built from the synthetic rows below, and
additionally compare whole records with the pre-Phase 2 SQL run through psycopg (keys,
key order, value types). They passed against the legacy implementation before the reads
moved to the shared engine. Appointment writes (create, update, cancel) stay on
database.get_connection(); the mixed-path tests use both, and the overlap rules stay
covered by tests/test_pg_appointments.py.
"""

import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import column, event, table
from sqlalchemy import exc as sa_exc

import appointment_service
import db_engine
import main
from appointment_service import (
    cancel_appointment,
    create_appointment,
    get_all_appointments,
    get_appointment_by_id,
    update_appointment,
)
from auth_dependencies import get_current_user
from models import AppointmentCreate, AppointmentUpdate
from tests.pg_helpers import verified_connection, verified_engine

FIELDS = [
    "id",
    "patient_id",
    "provider_id",
    "starts_at",
    "ends_at",
    "status",
    "reason",
    "created_at",
]

# The pre-Phase 2 queries, unchanged apart from whitespace.
LEGACY_LIST = (
    "SELECT id, patient_id, provider_id, starts_at, ends_at, status, reason, created_at "
    "FROM appointments WHERE 1 = 1"
)
LEGACY_BY_ID = (
    "SELECT id, patient_id, provider_id, starts_at, ends_at, status, reason, created_at "
    "FROM appointments WHERE id = %s"
)


def legacy_list(conn, patient_id=None, provider_id=None, status=None) -> list[dict]:
    query, values = LEGACY_LIST, []
    if patient_id is not None:
        query += " AND patient_id = %s"
        values.append(patient_id)
    if provider_id is not None:
        query += " AND provider_id = %s"
        values.append(provider_id)
    if status is not None:
        query += " AND status = %s"
        values.append(status)
    query += " ORDER BY starts_at, id"
    return [dict(zip(FIELDS, row, strict=True)) for row in conn.execute(query, values)]


def assert_same_records(actual: list[dict], expected: list[dict]) -> None:
    assert actual == expected
    assert [list(item) for item in actual] == [FIELDS] * len(expected)
    assert [[type(v) for v in item.values()] for item in actual] == [
        [type(v) for v in item.values()] for item in expected
    ]


# --- fixtures ----------------------------------------------------------------------------


@pytest.fixture
def direct(disposable_database):
    with verified_connection(disposable_database) as conn:
        yield conn


PLUS_TWO = timezone(timedelta(hours=2))
MINUS_SIX = timezone(timedelta(hours=-6))

# (patient, provider, starts_at, ends_at, status, reason), inserted in this order. Their
# ids do not follow starts_at order, two share a start (the id breaks the tie), the
# offsets differ, one reason is NULL, and every status is present.
SYNTHETIC = {
    "a1": (
        "p1",
        "r1",
        datetime(2031, 5, 1, 10, 0, tzinfo=UTC),
        datetime(2031, 5, 1, 10, 30, tzinfo=UTC),
        "scheduled",
        "Synthetic check-up",
    ),
    "a2": (
        "p2",
        "r1",
        datetime(2031, 5, 1, 9, 0, tzinfo=UTC),
        datetime(2031, 5, 1, 9, 30, tzinfo=UTC),
        "completed",
        None,
    ),
    "a3": (
        "p1",
        "r2",
        datetime(2031, 5, 1, 10, 0, tzinfo=UTC),
        datetime(2031, 5, 1, 11, 0, tzinfo=UTC),
        "cancelled",
        "Synthetic follow-up",
    ),
    "a4": (
        "p2",
        "r2",
        datetime(2031, 5, 2, 8, 0, tzinfo=PLUS_TWO),
        datetime(2031, 5, 2, 9, 0, tzinfo=PLUS_TWO),
        "scheduled",
        "Synthetic review",
    ),
    "a5": (
        "p1",
        "r1",
        datetime(2031, 4, 30, 23, 0, tzinfo=MINUS_SIX),
        datetime(2031, 4, 30, 23, 45, tzinfo=MINUS_SIX),
        "scheduled",
        "Synthetic early visit",
    ),
}
# starts_at order, with a1 before a3 by id: 05:00Z, 09:00Z, 10:00Z, 10:00Z, next day 06:00Z.
BY_START = ["a5", "a2", "a1", "a3", "a4"]


@pytest.fixture
def seeded(direct):
    """Patients, providers and appointments; returns {name: id} for all of them."""
    ids = {}
    for name, (first, last) in {"p1": ("Rowan", "Ledger"), "p2": ("Ada", "Quill")}.items():
        ids[name] = direct.execute(
            "INSERT INTO patients (first_name, last_name, date_of_birth, gender) "
            "VALUES (%s, %s, '1980-01-01', 'other') RETURNING id",
            (first, last),
        ).fetchone()[0]
    for name, last in {"r1": "Synthetica", "r2": "Placebo"}.items():
        ids[name] = direct.execute(
            "INSERT INTO providers (first_name, last_name, specialty) "
            "VALUES ('Dana', %s, 'Synthetic Medicine') RETURNING id",
            (last,),
        ).fetchone()[0]
    for name, (patient, provider, starts, ends, status, reason) in SYNTHETIC.items():
        ids[name] = direct.execute(
            "INSERT INTO appointments (patient_id, provider_id, starts_at, ends_at, status, "
            "reason) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (ids[patient], ids[provider], starts, ends, status, reason),
        ).fetchone()[0]
    # A new version of a1 is stored after the others, so storage order differs from id
    # order and only ORDER BY gives the documented order.
    direct.execute("UPDATE appointments SET reason = reason WHERE id = %s", (ids["a1"],))
    return ids


def names(rows, ids) -> list[str]:
    by_id = {value: key for key, value in ids.items() if key.startswith("a")}
    return [by_id[row["id"]] for row in rows]


ADMIN = {
    "id": None,
    "username": "synthetic.appointment.admin",
    "email": "appointment.admin@example.org",
    "role": "admin",
    "is_active": True,
}


@pytest.fixture
def client():
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_current_user] = lambda: ADMIN
    try:
        yield TestClient(main.app, raise_server_exceptions=False)
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


# --- listing -------------------------------------------------------------------------------


def test_an_empty_table_gives_an_empty_list(direct):
    assert legacy_list(direct) == []
    assert get_all_appointments() == []


def test_the_list_is_ordered_by_start_then_id(direct, seeded):
    stored = [row[0] for row in direct.execute("SELECT id FROM appointments").fetchall()]
    assert stored != sorted(stored)  # precondition: storage order is not id order

    rows = get_all_appointments()

    assert names(rows, seeded) == BY_START
    assert_same_records(rows, legacy_list(direct))


def test_records_carry_the_stored_values_and_types(seeded):
    by_name = dict(zip(BY_START, get_all_appointments(), strict=True))

    for name, (patient, provider, starts, ends, status, reason) in SYNTHETIC.items():
        record = by_name[name]
        assert list(record) == FIELDS
        assert record["id"] == seeded[name]
        assert (record["patient_id"], record["provider_id"]) == (
            seeded[patient],
            seeded[provider],
        )
        # Same instants, whatever offset they were written with; always timezone-aware.
        assert (record["starts_at"], record["ends_at"]) == (starts, ends)
        assert record["starts_at"].tzinfo is not None
        assert record["created_at"].tzinfo is not None
        assert (record["status"], record["reason"]) == (status, reason)
        assert type(record["id"]) is int
        assert type(record["status"]) is str


FILTERS = {
    "patient": ({"patient_id": "p1"}, ["a5", "a1", "a3"]),
    "provider": ({"provider_id": "r1"}, ["a5", "a2", "a1"]),
    "scheduled": ({"status": "scheduled"}, ["a5", "a1", "a4"]),
    "completed": ({"status": "completed"}, ["a2"]),
    "cancelled-are-not-hidden": ({"status": "cancelled"}, ["a3"]),
    "patient-and-provider": ({"patient_id": "p1", "provider_id": "r1"}, ["a5", "a1"]),
    "all-three": (
        {"patient_id": "p1", "provider_id": "r1", "status": "scheduled"},
        ["a5", "a1"],
    ),
    "provider-and-status": ({"provider_id": "r2", "status": "scheduled"}, ["a4"]),
    "combination-without-match": ({"patient_id": "p2", "status": "cancelled"}, []),
}


@pytest.mark.parametrize(("filters", "expected"), FILTERS.values(), ids=FILTERS.keys())
def test_filters_combine_with_and(direct, seeded, filters, expected):
    resolved = {k: seeded[v] if k != "status" else v for k, v in filters.items()}

    rows = get_all_appointments(**resolved)

    assert names(rows, seeded) == expected
    assert_same_records(rows, legacy_list(direct, **resolved))


@pytest.mark.parametrize(
    "filters",
    [
        {"patient_id": 2_147_483_647},
        {"provider_id": 2_147_483_647},
        # 0 is a value, not "no filter": an explicit None check, not truthiness.
        {"patient_id": 0},
        {"provider_id": 0},
        {"status": ""},
        # Status matching is exact and case-sensitive.
        {"status": "Scheduled"},
        {"status": "unknown"},
    ],
    ids=["no-patient", "no-provider", "patient-0", "provider-0", "empty-status", "case", "other"],
)
def test_nonmatching_and_falsy_filters_match_nothing(direct, seeded, filters):
    assert get_all_appointments(**filters) == []
    assert legacy_list(direct, **filters) == []


def test_omitted_and_none_filters_mean_no_filter(seeded):
    expected = names(get_all_appointments(), seeded)

    assert names(get_all_appointments(None, None, None), seeded) == expected
    assert expected == BY_START


# --- lookup --------------------------------------------------------------------------------


def test_lookup_returns_each_stored_record(direct, seeded):
    for name in SYNTHETIC:
        expected = [
            dict(zip(FIELDS, row, strict=True))
            for row in direct.execute(LEGACY_BY_ID, (seeded[name],))
        ]
        assert_same_records([get_appointment_by_id(seeded[name])], expected)


@pytest.mark.parametrize("missing", [0, -1, 2_147_483_647])
def test_lookup_of_a_missing_appointment_is_none(seeded, missing):
    assert get_appointment_by_id(missing) is None


# --- return contract -----------------------------------------------------------------------
# The previous reads returned a plain list of plain dicts, and a plain dict or None, built
# while the connection was open. These checks hold for the previous implementation too.


def test_an_empty_list_is_a_plain_list(direct):
    result = get_all_appointments()

    assert type(result) is list
    assert result == []


def test_the_list_is_a_plain_list_of_plain_dicts_usable_after_release(direct, seeded):
    result = get_all_appointments()
    assert db_engine.get_engine().pool.checkedout() == 0  # the connection is back

    assert type(result) is list
    assert [type(record) for record in result] == [dict] * len(SYNTHETIC)
    assert len(result) == len(SYNTHETIC)
    assert names([result[0], result[-1]], seeded) == [BY_START[0], BY_START[-1]]
    assert names(result[1:3], seeded) == BY_START[1:3]
    first_pass = [record["id"] for record in result]
    assert [record["id"] for record in result] == first_pass  # iterable more than once
    assert_same_records(result, legacy_list(direct))

    # Each record is the caller's own copy, as before.
    result[0]["reason"] = "changed by the caller"
    assert get_all_appointments() == legacy_list(direct)


def test_a_looked_up_record_is_a_plain_dict_usable_after_release(direct, seeded):
    record = get_appointment_by_id(seeded["a1"])
    assert db_engine.get_engine().pool.checkedout() == 0  # the connection is back

    expected = dict(
        zip(FIELDS, direct.execute(LEGACY_BY_ID, (seeded["a1"],)).fetchone(), strict=True)
    )
    assert type(record) is dict
    assert list(record) == FIELDS
    assert record == expected
    assert record["starts_at"].tzinfo is not None

    record["status"] = "changed by the caller"
    assert get_appointment_by_id(seeded["a1"]) == expected


# --- REST ----------------------------------------------------------------------------------


def test_rest_list_and_lookup_keep_their_responses(client, seeded):
    listed = client.get(
        "/appointments",
        params={"patient_id": seeded["p1"], "provider_id": seeded["r1"], "status": "scheduled"},
    )
    one = client.get(f"/appointments/{seeded['a2']}")
    missing = client.get("/appointments/2147483647")

    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [seeded["a5"], seeded["a1"]]
    assert list(listed.json()[0]) == [
        "patient_id",
        "provider_id",
        "starts_at",
        "ends_at",
        "reason",
        "id",
        "status",
        "created_at",
    ]
    assert one.status_code == 200
    assert one.json()["reason"] is None
    assert datetime.fromisoformat(one.json()["starts_at"]) == SYNTHETIC["a2"][2]
    assert (missing.status_code, missing.json()) == (404, {"detail": "Appointment not found"})


def test_rest_validation_is_unchanged(client, seeded):
    assert client.get("/appointments", params={"status": "Scheduled"}).status_code == 422
    assert client.get("/appointments", params={"patient_id": "x"}).status_code == 422
    assert client.get("/appointments/x").status_code == 422


# --- transactions and connections ----------------------------------------------------------

READS = {
    "list": lambda ids: get_all_appointments(patient_id=ids["p1"], status="scheduled"),
    "lookup": lambda ids: get_appointment_by_id(ids["a1"]),
}


@pytest.mark.parametrize("read", READS.values(), ids=READS.keys())
def test_reads_run_in_one_read_only_transaction_with_the_timeouts(seeded, read):
    engine = db_engine.get_engine()
    events, statements = [], []

    def on_begin(conn):
        events.append("begin")

    def on_commit(conn):
        events.append("commit")

    def on_rollback(conn):
        events.append("rollback")

    def before_execute(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, conn.connection.driver_connection.read_only))

    listeners = [
        ("begin", on_begin),
        ("commit", on_commit),
        ("rollback", on_rollback),
        ("before_cursor_execute", before_execute),
    ]
    for name, listener in listeners:
        event.listen(engine, name, listener)
    try:
        result = read(seeded)
    finally:
        for name, listener in listeners:
            event.remove(engine, name, listener)

    assert result  # materialised, usable after the connection was returned
    assert events == ["begin", "rollback"]
    assert "'statement_timeout'" in statements[0][0]  # the engine's timeout policy, first
    assert sum("set_config" in statement for statement, _ in statements) == 1
    assert all(statement.lstrip().startswith("SELECT") for statement, _ in statements)
    assert all(read_only for _, read_only in statements)
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize("read", READS.values(), ids=READS.keys())
def test_a_failing_read_propagates_and_returns_its_connection(monkeypatch, seeded, read):
    # Fault injection: the same columns from a table that does not exist.
    monkeypatch.setattr(
        appointment_service,
        "APPOINTMENTS",
        table("missing_appointments", *(column(field) for field in FIELDS)),
    )

    with pytest.raises(sa_exc.ProgrammingError) as raised:
        read(seeded)

    assert isinstance(raised.value.orig, psycopg.errors.UndefinedTable)
    assert db_engine.get_engine().pool.checkedout() == 0


@pytest.mark.parametrize("read", READS.values(), ids=READS.keys())
def test_results_are_read_before_the_connection_is_released(monkeypatch, seeded, read):
    # Every result is closed as soon as read_only_connection() exits, as the results of a
    # server-side cursor would be. A read that fetched rows after the block, instead of
    # inside it, would fail here.
    opened = []
    real_read_only_connection = appointment_service.read_only_connection

    class RecordingConnection:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, *args, **kwargs):
            result = self._conn.execute(*args, **kwargs)
            opened.append(result)
            return result

    @contextmanager
    def closing_results():
        with real_read_only_connection() as conn:
            yield RecordingConnection(conn)
        for result in opened:
            result.close()

    monkeypatch.setattr(appointment_service, "read_only_connection", closing_results)

    result = read(seeded)

    assert len(opened) == 1
    assert result
    assert db_engine.get_engine().pool.checkedout() == 0


@pytest.fixture
def one_connection_engine(disposable_database, monkeypatch):
    """The process engine, replaced for one test by a one-connection pool with a 1 s wait."""
    with verified_engine(
        disposable_database,
        DB_POOL_SIZE="1",
        DB_POOL_MAX_OVERFLOW="0",
        DB_POOL_TIMEOUT_SECONDS="1",
    ) as engine:
        monkeypatch.setattr(db_engine, "_engine", engine)
        yield engine


@pytest.mark.parametrize("path", ["/appointments", "/appointments/{a1}"])
def test_an_exhausted_pool_gives_a_plain_500_within_the_wait_then_recovers(
    client, seeded, one_connection_engine, path
):
    url = path.format(a1=seeded["a1"])
    held = one_connection_engine.connect()  # the only connection
    try:
        started = time.monotonic()
        response = client.get(url)
        waited = time.monotonic() - started
    finally:
        held.close()

    assert (response.status_code, response.text) == (500, "Internal Server Error")
    assert 0.9 <= waited < 10
    assert one_connection_engine.pool.checkedout() == 0
    assert client.get(url).status_code == 200


def test_concurrent_reads_on_a_one_connection_pool_take_it_in_turn(seeded, one_connection_engine):
    # Two reads released together, with no overflow allowed: they can only use the one
    # connection in turn. This shows brief contention waits rather than fails, and that
    # both reads went through the pool; it does not show that they overlapped.
    start = threading.Barrier(2, timeout=10)
    results, checkouts = [], []
    record_checkout = lambda *args: checkouts.append(None)  # noqa: E731
    event.listen(one_connection_engine, "checkout", record_checkout)

    def read():
        start.wait()
        results.append(len(get_all_appointments()))

    threads = [threading.Thread(target=read) for _ in range(2)]
    for thread in threads:
        thread.start()
    try:
        for thread in threads:
            thread.join(10)
    finally:
        event.remove(one_connection_engine, "checkout", record_checkout)

    assert not any(thread.is_alive() for thread in threads)
    assert results == [len(SYNTHETIC)] * 2
    assert len(checkouts) == 2  # one checkout of the single pooled connection per read
    assert one_connection_engine.pool.checkedout() == 0


# --- legacy writes and pooled reads together -------------------------------------------------


def test_a_legacy_create_update_and_cancel_are_visible_to_pooled_reads(seeded):
    starts = datetime(2031, 6, 1, 14, 0, tzinfo=UTC)
    created = create_appointment(
        AppointmentCreate(
            patient_id=seeded["p2"],
            provider_id=seeded["r1"],
            starts_at=starts,
            ends_at=starts + timedelta(minutes=30),
            reason="Synthetic mixed path",
        )
    )
    assert get_appointment_by_id(created["id"]) == created

    updated = update_appointment(
        created["id"],
        AppointmentUpdate(
            starts_at=starts + timedelta(hours=1),
            ends_at=starts + timedelta(hours=2),
            status="completed",
            reason=None,
        ),
    )
    assert get_appointment_by_id(created["id"]) == updated
    assert updated["id"] in [row["id"] for row in get_all_appointments(status="completed")]

    cancelled = cancel_appointment(created["id"])
    assert get_appointment_by_id(created["id"]) == cancelled
    assert cancelled["status"] == "cancelled"
    assert get_all_appointments(patient_id=seeded["p2"], status="cancelled") == [cancelled]


def test_a_rejected_overlapping_booking_changes_nothing_the_reads_see(seeded):
    before = get_all_appointments()
    starts, ends, *_ = SYNTHETIC["a1"][2:]

    with pytest.raises(psycopg.errors.ExclusionViolation):
        create_appointment(
            AppointmentCreate(
                patient_id=seeded["p2"],
                provider_id=seeded["r1"],
                starts_at=starts + timedelta(minutes=10),
                ends_at=ends,
            )
        )

    assert get_all_appointments() == before
