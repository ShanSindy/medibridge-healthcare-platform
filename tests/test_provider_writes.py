"""
Provider writes (create, update, delete) on db_engine.write_transaction(), against this
session's disposable database.

The contract tests have fixed expectations and were run against the previous
database.get_connection() implementation before the change: the returned dictionaries
(keys, order, value types), the persisted rows, None and False for missing providers,
the violated constraint of a rejected write with nothing persisted, the REST status
codes and bodies, and concurrent writers waiting for and then honouring each other's
outcome. The remaining tests cover what the move adds: one managed read-write
transaction per write with the engine's timeouts, rollback of the whole write when the
block fails, SQLAlchemy-wrapped errors and their mappings, and the lock timeout.
"""

import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg import errors
from sqlalchemy import exc as sa_exc

import db_engine
import main
import provider_service
from auth_dependencies import get_current_user
from db_errors import sqlstate, violated_constraint
from models import ProviderCreate
from provider_service import create_provider, delete_provider, update_provider
from routers.providers import create_provider_endpoint
from tests.pg_helpers import verified_connection, verified_engine, wait_until

KEYS = ["id", "first_name", "last_name", "specialty", "email", "phone", "created_at"]
DUPLICATE_EMAIL = {"detail": "A provider with this email already exists."}
HAS_APPOINTMENTS = {"detail": "Provider has appointments and cannot be deleted."}
NOT_FOUND = {"detail": "Provider not found"}
# A rejected write raises a psycopg error (previously) or its SQLAlchemy wrapper (now).
DATABASE_ERRORS = (psycopg.Error, sa_exc.DBAPIError)


# --- fixtures and helpers ----------------------------------------------------------------


@pytest.fixture
def direct(disposable_database):
    with verified_connection(disposable_database) as conn:
        yield conn


@contextmanager
def _engine(disposable_database, monkeypatch, *, statement, lock):
    with verified_engine(
        disposable_database,
        DB_POOL_SIZE="1",
        DB_POOL_MAX_OVERFLOW="0",
        DB_POOL_TIMEOUT_SECONDS="5",
        DB_STATEMENT_TIMEOUT_SECONDS=statement,
        DB_LOCK_TIMEOUT_SECONDS=lock,
    ) as test_engine:
        monkeypatch.setattr(db_engine, "_engine", test_engine)
        yield test_engine


@pytest.fixture
def engine(disposable_database, monkeypatch):
    """The process engine, replaced by a one-connection one with 2 s / 1 s timeouts."""
    with _engine(disposable_database, monkeypatch, statement="2", lock="1") as test_engine:
        yield test_engine


@pytest.fixture
def waiting_engine(disposable_database, monkeypatch):
    """A one-connection engine whose lock timeout outlasts the concurrency tests' waits."""
    with _engine(disposable_database, monkeypatch, statement="30", lock="20") as test_engine:
        yield test_engine


@pytest.fixture
def client():
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": None,
        "username": "synthetic.provider.admin",
        "email": "provider.admin@example.org",
        "role": "admin",
        "is_active": True,
    }
    try:
        yield TestClient(main.app, raise_server_exceptions=False)
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


def synthetic(last_name="Synthetica", email=None, phone=None, first_name="Dana") -> ProviderCreate:
    return ProviderCreate(
        first_name=first_name,
        last_name=last_name,
        specialty="Synthetic Medicine",
        email=email,
        phone=phone,
    )


def body(last_name="Synthetica", email=None) -> dict:
    return {
        "first_name": "Dana",
        "last_name": last_name,
        "specialty": "Synthetic Medicine",
        "email": email,
    }


def stored(direct, provider_id):
    row = direct.execute(
        "SELECT id, first_name, last_name, specialty, email, phone, created_at "
        "FROM providers WHERE id = %s",
        (provider_id,),
    ).fetchone()
    return None if row is None else dict(zip(KEYS, row, strict=True))


def provider_rows(direct):
    return direct.execute(
        "SELECT id, first_name, last_name, specialty, email, phone, created_at "
        "FROM providers ORDER BY id"
    ).fetchall()


def add_appointment(direct, provider_id) -> int:
    patient_id = direct.execute(
        "INSERT INTO patients (first_name, last_name, date_of_birth, gender) "
        "VALUES ('Writes', 'Synthetic', '1990-01-01', 'unknown') RETURNING id"
    ).fetchone()[0]
    starts_at = datetime(2031, 1, 6, 9, 0, tzinfo=UTC)
    return direct.execute(
        "INSERT INTO appointments (patient_id, provider_id, starts_at, ends_at) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (patient_id, provider_id, starts_at, starts_at + timedelta(minutes=30)),
    ).fetchone()[0]


def no_open_transactions(direct) -> bool:
    count = direct.execute(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND pid <> pg_backend_pid() "
        "AND state LIKE 'idle in transaction%'"
    ).fetchone()[0]
    return count == 0


# --- contract: returned values and persisted rows ----------------------------------------


def test_create_returns_the_stored_row_as_a_plain_dict(direct):
    created = create_provider(synthetic(email="dana@example.org", phone="555-0101"))

    assert type(created) is dict
    assert list(created) == KEYS
    assert type(created["id"]) is int
    assert isinstance(created["created_at"], datetime)
    assert created["created_at"].tzinfo is not None
    assert created == stored(direct, created["id"])
    assert created["email"] == "dana@example.org"
    assert created["phone"] == "555-0101"


def test_create_without_optional_fields_stores_nulls(direct):
    created = create_provider(synthetic())

    assert (created["email"], created["phone"]) == (None, None)
    assert created == stored(direct, created["id"])


def test_update_returns_and_persists_every_field_and_keeps_id_and_created_at(direct):
    created = create_provider(synthetic(email="before@example.org", phone="555-0102"))

    updated = update_provider(
        created["id"],
        ProviderCreate(
            first_name="Robin",
            last_name="Hale",
            specialty="Dermatology",
            email="after@example.org",
            phone=None,
        ),
    )

    assert type(updated) is dict
    assert list(updated) == KEYS
    assert updated == stored(direct, created["id"])
    assert updated == {
        "id": created["id"],
        "first_name": "Robin",
        "last_name": "Hale",
        "specialty": "Dermatology",
        "email": "after@example.org",
        "phone": None,
        "created_at": created["created_at"],
    }


def test_update_of_a_missing_provider_is_none_and_changes_nothing(direct):
    kept = create_provider(synthetic(email="kept@example.org"))
    before = provider_rows(direct)

    assert update_provider(kept["id"] + 1000, synthetic("Other")) is None
    assert provider_rows(direct) == before


def test_delete_removes_only_that_provider_and_reports_it(direct):
    gone = create_provider(synthetic("Gone"))
    kept = create_provider(synthetic("Kept"))

    assert delete_provider(gone["id"]) is True
    assert stored(direct, gone["id"]) is None
    assert stored(direct, kept["id"]) == kept
    assert delete_provider(gone["id"]) is False


def test_delete_of_a_missing_provider_is_false_and_changes_nothing(direct):
    create_provider(synthetic())
    before = provider_rows(direct)

    assert delete_provider(999_999) is False
    assert provider_rows(direct) == before


# --- contract: rejected writes persist nothing -------------------------------------------


def test_a_duplicate_email_on_create_names_the_constraint_and_persists_nothing(direct):
    create_provider(synthetic(email="taken@example.org"))
    before = provider_rows(direct)

    with pytest.raises(DATABASE_ERRORS) as caught:
        create_provider(synthetic("Second", email="taken@example.org"))

    assert violated_constraint(caught.value) == "providers_email_key"
    assert provider_rows(direct) == before


def test_a_duplicate_email_on_update_names_the_constraint_and_changes_nothing(direct):
    create_provider(synthetic(email="taken@example.org"))
    other = create_provider(synthetic("Other", email="other@example.org"))
    before = provider_rows(direct)

    with pytest.raises(DATABASE_ERRORS) as caught:
        update_provider(other["id"], synthetic("Other", email="taken@example.org"))

    assert violated_constraint(caught.value) == "providers_email_key"
    assert provider_rows(direct) == before


def test_deleting_a_provider_with_appointments_names_the_constraint_and_keeps_it(direct):
    provider = create_provider(synthetic())
    add_appointment(direct, provider["id"])

    with pytest.raises(DATABASE_ERRORS) as caught:
        delete_provider(provider["id"])

    assert violated_constraint(caught.value) == "appointments_provider_id_fkey"
    assert stored(direct, provider["id"]) == provider


def test_an_unmapped_constraint_violation_is_not_translated_by_the_router(direct):
    # The request model never lets a blank name through; built without validation, it
    # reaches PostgreSQL's CHECK constraint, which no endpoint maps to a client error.
    blank = ProviderCreate.model_construct(
        first_name=" ",
        last_name="Synthetica",
        specialty="Synthetic Medicine",
        email=None,
        phone=None,
    )

    with pytest.raises(DATABASE_ERRORS) as caught:
        create_provider_endpoint(blank)

    assert not isinstance(caught.value, HTTPException)
    assert violated_constraint(caught.value) == "providers_first_name_not_blank"
    assert provider_rows(direct) == []


# --- contract: REST responses ------------------------------------------------------------


def test_rest_writes_keep_their_status_codes_and_bodies(client, direct):
    created = client.post("/providers", json=body(email="rest@example.org"))
    assert created.status_code == 201
    provider = created.json()
    # The response follows the Provider model's field order.
    assert list(provider) == [
        "first_name",
        "last_name",
        "specialty",
        "email",
        "phone",
        "id",
        "created_at",
    ]

    assert client.post("/providers", json=body("Copy", email="rest@example.org")).json() == (
        DUPLICATE_EMAIL
    )
    other = client.post("/providers", json=body("Other", email="other@example.org")).json()
    clash = client.put(f"/providers/{other['id']}", json=body("Other", email="rest@example.org"))
    assert (clash.status_code, clash.json()) == (409, DUPLICATE_EMAIL)

    missing = client.put("/providers/999999", json=body())
    assert (missing.status_code, missing.json()) == (404, NOT_FOUND)
    missing = client.delete("/providers/999999")
    assert (missing.status_code, missing.json()) == (404, NOT_FOUND)

    add_appointment(direct, provider["id"])
    referenced = client.delete(f"/providers/{provider['id']}")
    assert (referenced.status_code, referenced.json()) == (409, HAS_APPOINTMENTS)

    removed = client.delete(f"/providers/{other['id']}")
    assert (removed.status_code, removed.content) == (204, b"")
    assert [row[0] for row in provider_rows(direct)] == [provider["id"]]


# --- contract: concurrent writers --------------------------------------------------------


@contextmanager
def holding(disposable_database, sql, params=()):
    """
    An uncommitted write on its own connection. The server ends the holder by itself if
    the test does not (idle_in_transaction_session_timeout), whatever the engine does.
    """
    with verified_connection(disposable_database) as holder:
        holder.autocommit = False
        holder.execute("SET idle_in_transaction_session_timeout = '20s'")
        holder.execute(sql, params)
        yield holder


def in_background(call):
    outcome = {}

    def run():
        try:
            outcome["response"] = call()
        except BaseException as exc:  # reported by the test, never swallowed
            outcome["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def blocked_by(direct, holder) -> bool:
    holder_pid = holder.info.backend_pid
    waiting = direct.execute(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND %s = ANY(pg_blocking_pids(pid))",
        (holder_pid,),
    ).fetchone()[0]
    return waiting > 0


def finished(thread, outcome):
    thread.join(timeout=60)
    assert not thread.is_alive(), "the worker did not finish"
    assert "error" not in outcome, outcome.get("error")
    return outcome["response"]


INSERT_HELD = (
    "INSERT INTO providers (first_name, last_name, specialty, email) "
    "VALUES ('Held', 'Synthetic', 'Testing', %s)"
)


@pytest.mark.parametrize("holder_ends", ["commit", "rollback"])
def test_a_concurrent_create_with_the_same_email_waits_for_the_first_writer(
    disposable_database, waiting_engine, client, direct, holder_ends
):
    with holding(disposable_database, INSERT_HELD, ("race@example.org",)) as holder:
        thread, outcome = in_background(
            lambda: client.post("/providers", json=body("Racer", email="race@example.org"))
        )
        assert wait_until(lambda: blocked_by(direct, holder))
        getattr(holder, holder_ends)()
        response = finished(thread, outcome)

    names = [row[2] for row in provider_rows(direct)]
    if holder_ends == "commit":
        assert (response.status_code, response.json()) == (409, DUPLICATE_EMAIL)
        assert names == ["Synthetic"]
    else:
        assert response.status_code == 201
        assert names == ["Racer"]
    assert no_open_transactions(direct)


def test_a_delete_waiting_for_a_new_appointment_reports_it_once_committed(
    disposable_database, waiting_engine, client, direct
):
    provider = create_provider(synthetic())
    patient_id = direct.execute(
        "INSERT INTO patients (first_name, last_name, date_of_birth, gender) "
        "VALUES ('Writes', 'Synthetic', '1990-01-01', 'unknown') RETURNING id"
    ).fetchone()[0]
    starts_at = datetime(2031, 1, 6, 9, 0, tzinfo=UTC)
    booking = (
        "INSERT INTO appointments (patient_id, provider_id, starts_at, ends_at) "
        "VALUES (%s, %s, %s, %s)"
    )
    params = (patient_id, provider["id"], starts_at, starts_at + timedelta(minutes=30))

    with holding(disposable_database, booking, params) as holder:
        thread, outcome = in_background(lambda: client.delete(f"/providers/{provider['id']}"))
        assert wait_until(lambda: blocked_by(direct, holder))
        holder.commit()
        response = finished(thread, outcome)

    assert (response.status_code, response.json()) == (409, HAS_APPOINTMENTS)
    assert stored(direct, provider["id"]) == provider
    assert no_open_transactions(direct)


# --- the move: one managed transaction per write -----------------------------------------


def observe_write_transaction(monkeypatch, then=None):
    """Wraps provider_service.write_transaction to record each block's settings."""
    real = provider_service.write_transaction
    seen = []

    @contextmanager
    def observed():
        with real() as conn:
            seen.append(
                tuple(
                    conn.exec_driver_sql(f"SHOW {name}").scalar_one()
                    for name in ("transaction_read_only", "statement_timeout", "lock_timeout")
                )
            )
            yield conn
            if then is not None:
                then()

    monkeypatch.setattr(provider_service, "write_transaction", observed)
    return seen


def test_each_write_is_one_managed_read_write_transaction_with_the_timeouts(
    monkeypatch, engine, direct
):
    seen = observe_write_transaction(monkeypatch)

    created = create_provider(synthetic(email="managed@example.org"))
    update_provider(created["id"], synthetic("Updated", email="managed@example.org"))
    update_provider(created["id"] + 1000, synthetic())
    delete_provider(created["id"])
    delete_provider(created["id"])

    assert seen == [("off", "2s", "1s")] * 5
    assert engine.pool.checkedout() == 0
    assert no_open_transactions(direct)


def fail_after_the_write():
    raise RuntimeError("synthetic failure after the write, inside its transaction")


def test_a_failure_inside_the_block_rolls_the_write_back(monkeypatch, engine, direct):
    kept = create_provider(synthetic("Kept", email="kept@example.org"))
    before = provider_rows(direct)
    observe_write_transaction(monkeypatch, then=fail_after_the_write)

    for write in (
        lambda: create_provider(synthetic("New", email="new@example.org")),
        lambda: update_provider(kept["id"], synthetic("Changed", email="changed@example.org")),
        lambda: delete_provider(kept["id"]),
    ):
        with pytest.raises(RuntimeError, match="synthetic failure"):
            write()
        assert provider_rows(direct) == before

    assert engine.pool.checkedout() == 0
    assert no_open_transactions(direct)


def test_rejected_writes_raise_the_wrapped_error_and_release_the_connection(engine, direct):
    provider = create_provider(synthetic(email="taken@example.org"))
    other = create_provider(synthetic("Other"))
    add_appointment(direct, provider["id"])

    taken = synthetic(email="taken@example.org")
    referenced = (errors.ForeignKeyViolation, errors.RestrictViolation)
    rejected = [
        (lambda: create_provider(taken), errors.UniqueViolation),
        (lambda: update_provider(other["id"], taken), errors.UniqueViolation),
        (lambda: delete_provider(provider["id"]), referenced),
    ]
    for write, native in rejected:
        with pytest.raises(sa_exc.IntegrityError) as caught:
            write()
        assert isinstance(caught.value.orig, native)
        assert engine.pool.checkedout() == 0

    assert no_open_transactions(direct)
    # The one pooled connection is clean: the next write succeeds first time.
    assert delete_provider(other["id"]) is True


def test_a_write_waiting_too_long_for_a_lock_is_a_plain_500_and_changes_nothing(
    monkeypatch, disposable_database, engine, client, direct
):
    provider = create_provider(synthetic(email="locked@example.org"))

    # Record what leaves each write's block, so the 500 is shown to be the lock timeout.
    real = provider_service.write_transaction
    escaped = []

    @contextmanager
    def recording():
        try:
            with real() as conn:
                yield conn
        except BaseException as exc:
            escaped.append(exc)
            raise

    monkeypatch.setattr(provider_service, "write_transaction", recording)

    for request in (
        lambda: client.put(f"/providers/{provider['id']}", json=body("Changed")),
        lambda: client.delete(f"/providers/{provider['id']}"),
    ):
        with verified_connection(disposable_database) as holder:
            holder.autocommit = False
            # The holder's own server-side bound ends it if nothing else does.
            holder.execute("SET idle_in_transaction_session_timeout = '5s'")
            holder.execute("SELECT id FROM providers WHERE id = %s FOR UPDATE", (provider["id"],))
            started = time.monotonic()
            response = request()
            waited = time.monotonic() - started
            holder.rollback()

        assert (response.status_code, response.text) == (500, "Internal Server Error")
        failure = escaped.pop()
        assert isinstance(failure, sa_exc.OperationalError)
        assert isinstance(failure.orig, errors.LockNotAvailable)
        assert sqlstate(failure) == "55P03"
        assert 0.8 <= waited < 4  # waited for the 1 s lock timeout, not the holder's 5 s
        assert stored(direct, provider["id"]) == provider
        assert engine.pool.checkedout() == 0
