"""
Write infrastructure (Phase 2 Milestone 7): error translation for native and wrapped
psycopg errors, db_engine.write_transaction(), and the optional libpq keepalives.

The integration tests run against this session's disposable database only, through
identity-checked connections and engines. No production write path uses
write_transaction() yet; these tests use synthetic rows and tables.
"""

import re
import time
from contextlib import suppress
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from psycopg import errors
from psycopg.pq import TransactionStatus
from sqlalchemy import exc as sa_exc
from sqlalchemy import text

import db_engine
from db_errors import psycopg_error, raise_for_constraint, sqlstate, violated_constraint
from routers.appointments import APPOINTMENT_REFERENCE_CONSTRAINTS
from routers.providers import PROVIDER_WRITE_CONSTRAINTS
from tests.pg_helpers import backend_exists, verified_connection, verified_engine, wait_until

SECRET = "synthetic-sensitive-value-0451"


class FakeError:
    """Native psycopg errors whose diagnostics can be set without a database."""

    @staticmethod
    def of(kind, constraint):
        class Fake(kind):
            @property
            def diag(self):  # type: ignore[override]
                return SimpleNamespace(constraint_name=constraint)

        return Fake("synthetic")


def wrapped(native):
    """The SQLAlchemy form of a psycopg error, as the engine raises it."""
    kind = sa_exc.IntegrityError if isinstance(native, errors.IntegrityError) else sa_exc.DBAPIError
    return kind("INSERT ...", {"email": SECRET}, native, hide_parameters=True)


KNOWN = {"providers_email_key": (409, "A provider with this email already exists.")}


# --- error translation: native and wrapped -----------------------------------------------


@pytest.mark.parametrize("form", ["native", "wrapped"])
def test_a_known_constraint_maps_in_both_forms(form):
    native = FakeError.of(errors.UniqueViolation, "providers_email_key")
    exc = native if form == "native" else wrapped(native)

    with pytest.raises(HTTPException) as caught:
        raise_for_constraint(exc, KNOWN)

    assert (caught.value.status_code, caught.value.detail) == KNOWN["providers_email_key"]
    assert caught.value.__cause__ is exc
    assert SECRET not in str(caught.value.detail)


@pytest.mark.parametrize("form", ["native", "wrapped"])
@pytest.mark.parametrize("constraint", ["some_other_constraint", None])
def test_an_unknown_constraint_is_reraised_in_its_own_form(form, constraint):
    native = FakeError.of(errors.UniqueViolation, constraint)
    exc = native if form == "native" else wrapped(native)

    with pytest.raises(type(exc)) as caught:
        raise_for_constraint(exc, KNOWN)

    assert caught.value is exc


@pytest.mark.parametrize("form", ["native", "wrapped"])
def test_a_non_integrity_error_naming_a_known_constraint_is_never_mapped(form):
    # A lock timeout (SQLSTATE 55P03) is not a client error, whatever diag says.
    native = FakeError.of(errors.LockNotAvailable, "providers_email_key")
    exc = native if form == "native" else wrapped(native)

    with pytest.raises(type(exc)) as caught:
        raise_for_constraint(exc, KNOWN)

    assert caught.value is exc
    assert violated_constraint(exc) is None


def test_errors_that_are_not_from_psycopg_are_reraised():
    for exc in (ValueError("not a database error"), sa_exc.DBAPIError("x", {}, ValueError())):
        with pytest.raises(type(exc)):
            raise_for_constraint(exc, KNOWN)
        assert psycopg_error(exc) is None
        assert sqlstate(exc) is None


def test_sqlstate_and_constraint_are_read_through_orig():
    native = FakeError.of(errors.ExclusionViolation, "appointments_no_provider_overlap")
    for exc in (native, wrapped(native)):
        assert psycopg_error(exc) is native
        assert sqlstate(exc) == "23P01"
        assert violated_constraint(exc) == "appointments_no_provider_overlap"


# --- keepalive settings ------------------------------------------------------------------


def test_no_keepalive_settings_keep_libpq_defaults():
    assert db_engine.keepalive_settings({}) == {}
    assert db_engine.keepalive_settings({"DB_KEEPALIVES_IDLE_SECONDS": "  "}) == {}


def test_keepalive_settings_become_libpq_parameters():
    assert db_engine.keepalive_settings(
        {
            "DB_KEEPALIVES": "1",
            "DB_KEEPALIVES_IDLE_SECONDS": "60",
            "DB_KEEPALIVES_INTERVAL_SECONDS": "10",
            "DB_KEEPALIVES_COUNT": "5",
        }
    ) == {"keepalives": 1, "keepalives_idle": 60, "keepalives_interval": 10, "keepalives_count": 5}
    assert db_engine.keepalive_settings({"DB_KEEPALIVES": "0"}) == {"keepalives": 0}


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DB_KEEPALIVES", "2"),
        ("DB_KEEPALIVES", "yes"),
        ("DB_KEEPALIVES_IDLE_SECONDS", "0"),
        ("DB_KEEPALIVES_IDLE_SECONDS", "7201"),
        ("DB_KEEPALIVES_INTERVAL_SECONDS", "601"),
        ("DB_KEEPALIVES_COUNT", "-1"),
        ("DB_KEEPALIVES_COUNT", "1_0"),
        ("DB_KEEPALIVES_COUNT", chr(0xFF15)),  # a fullwidth digit five
    ],
)
def test_invalid_keepalive_settings_are_refused_without_echoing_the_value(name, value):
    with pytest.raises(db_engine.DatabaseConfigurationError) as caught:
        db_engine.keepalive_settings({name: value})

    # Only the setting's name and its rule, never the rejected value.
    assert re.fullmatch(
        rf"{name} must be (a whole number|between \d+ and \d+)\.", str(caught.value)
    )


def test_keepalive_timing_with_keepalives_disabled_is_refused():
    with pytest.raises(db_engine.DatabaseConfigurationError, match="DB_KEEPALIVES=0"):
        db_engine.keepalive_settings({"DB_KEEPALIVES": "0", "DB_KEEPALIVES_IDLE_SECONDS": "30"})


def test_keepalives_reach_the_connection(disposable_database):
    settings = {
        "DB_KEEPALIVES": "1",
        "DB_KEEPALIVES_IDLE_SECONDS": "45",
        "DB_KEEPALIVES_INTERVAL_SECONDS": "7",
        "DB_KEEPALIVES_COUNT": "4",
    }
    with verified_engine(disposable_database, **settings) as engine:
        with engine.connect() as conn:
            parameters = conn.connection.driver_connection.info.get_parameters()
    assert {k: parameters.get(k) for k in ("keepalives_idle", "keepalives_interval")} == {
        "keepalives_idle": "45",
        "keepalives_interval": "7",
    }
    # keepalives=1 is libpq's default, and libpq reports only non-default parameters.
    assert parameters.get("keepalives") in (None, "1")
    assert parameters.get("keepalives_count") == "4"


def test_an_invalid_keepalive_setting_stops_engine_creation():
    with pytest.raises(db_engine.DatabaseConfigurationError):
        db_engine.build_engine(
            {
                "DB_HOST": "h",
                "DB_PORT": "5432",
                "DB_NAME": "n",
                "DB_USER": "u",
                "DB_KEEPALIVES_COUNT": "0",
            }
        )


# --- write_transaction -------------------------------------------------------------------


@pytest.fixture
def engine(disposable_database, monkeypatch):
    """The process engine, replaced by a small identity-checked one with short timeouts."""
    with verified_engine(
        disposable_database,
        DB_POOL_SIZE="1",
        DB_POOL_MAX_OVERFLOW="0",
        DB_POOL_TIMEOUT_SECONDS="5",
        DB_STATEMENT_TIMEOUT_SECONDS="2",
        DB_LOCK_TIMEOUT_SECONDS="1",
    ) as test_engine:
        monkeypatch.setattr(db_engine, "_engine", test_engine)
        yield test_engine


INSERT_PROVIDER = text(
    "INSERT INTO providers (first_name, last_name, specialty, email) "
    "VALUES ('Dana', 'Synthetica', 'Synthetic Medicine', :email) RETURNING id"
)


def providers_with(disposable_database, email):
    with verified_connection(disposable_database) as conn:
        found = conn.execute("SELECT count(*) FROM providers WHERE email = %s", (email,))
        return found.fetchone()[0]


def write(*emails, then=None):
    """One write_transaction() inserting providers, then running `then(conn)` if given."""
    with db_engine.write_transaction() as conn:
        for email in emails:
            conn.execute(INSERT_PROVIDER, {"email": email})
        if then is not None:
            then(conn)


def fail(exc):
    def raising(conn):
        raise exc

    return raising


def test_a_successful_block_commits(disposable_database, engine):
    write("commit@example.org")

    assert providers_with(disposable_database, "commit@example.org") == 1
    assert engine.pool.checkedout() == 0


def test_an_exception_rolls_back_and_propagates_unchanged(disposable_database, engine):
    failure = RuntimeError("synthetic failure after the insert")

    with pytest.raises(RuntimeError) as caught:
        write("rollback@example.org", then=fail(failure))

    assert caught.value is failure
    assert providers_with(disposable_database, "rollback@example.org") == 0
    assert engine.pool.checkedout() == 0


def test_the_transaction_is_read_write_with_the_engine_timeouts(engine):
    with db_engine.write_transaction() as conn:
        read_only = conn.exec_driver_sql("SHOW transaction_read_only").scalar_one()
        statement = conn.exec_driver_sql("SHOW statement_timeout").scalar_one()
        lock = conn.exec_driver_sql("SHOW lock_timeout").scalar_one()
    assert (read_only, statement, lock) == ("off", "2s", "1s")


def test_an_inner_commit_is_refused_and_nothing_is_committed(disposable_database, engine):
    reached = []

    def commit_then_insert(conn):
        conn.commit()  # refused before it reaches PostgreSQL
        reached.append("after commit")
        conn.execute(INSERT_PROVIDER, {"email": "after-early-commit@example.org"})

    with pytest.raises(db_engine.TransactionOwnershipError, match="do not commit or roll back"):
        write("early@example.org", then=commit_then_insert)

    assert reached == []

    assert providers_with(disposable_database, "early@example.org") == 0
    assert providers_with(disposable_database, "after-early-commit@example.org") == 0
    assert engine.pool.checkedout() == 0


def commit_then_fail(conn):
    conn.commit()
    raise ValueError("never reached: the commit is refused first")


def test_an_inner_commit_followed_by_an_error_commits_nothing(disposable_database, engine):
    with pytest.raises(db_engine.TransactionOwnershipError):
        write("early-then-error@example.org", then=commit_then_fail)

    assert providers_with(disposable_database, "early-then-error@example.org") == 0


def test_an_inner_rollback_is_refused_and_the_whole_block_rolls_back(disposable_database, engine):
    reached = []

    def rollback_then_insert(conn):
        conn.rollback()  # refused at the call: the block decides how its transaction ends
        reached.append("after rollback")
        conn.execute(INSERT_PROVIDER, {"email": "after-inner-rollback@example.org"})

    with pytest.raises(db_engine.TransactionOwnershipError, match="do not commit or roll back"):
        write("before-inner-rollback@example.org", then=rollback_then_insert)

    assert reached == []  # nothing after the refused rollback ran

    assert providers_with(disposable_database, "before-inner-rollback@example.org") == 0
    assert providers_with(disposable_database, "after-inner-rollback@example.org") == 0
    assert engine.pool.checkedout() == 0


def refused_commit(conn):
    conn.commit()


def refused_rollback(conn):
    conn.rollback()


@pytest.mark.parametrize("refusal", [refused_commit, refused_rollback], ids=["commit", "rollback"])
def test_the_pooled_connection_comes_back_idle_after_a_refusal(
    disposable_database, engine, refusal
):
    with pytest.raises(db_engine.TransactionOwnershipError):
        write("refused@example.org", then=refusal)

    # The same single pooled connection is handed out next: it must be idle, not still
    # inside the refused transaction, and the next write must work first time.
    with engine.connect() as conn:
        status = conn.connection.driver_connection.info.transaction_status
    assert status == TransactionStatus.IDLE
    write("after-refusal@example.org")
    assert providers_with(disposable_database, "refused@example.org") == 0
    assert providers_with(disposable_database, "after-refusal@example.org") == 1


@pytest.mark.parametrize("refusal", [refused_commit, refused_rollback], ids=["commit", "rollback"])
def test_a_swallowed_refusal_still_commits_nothing(disposable_database, engine, refusal):
    def swallow_then_insert(conn):
        with suppress(db_engine.TransactionOwnershipError):
            refusal(conn)
        conn.execute(INSERT_PROVIDER, {"email": "after-swallowed@example.org"})

    with pytest.raises(sa_exc.SQLAlchemyError):  # the block can no longer commit
        write("before-swallowed@example.org", then=swallow_then_insert)

    assert providers_with(disposable_database, "before-swallowed@example.org") == 0
    assert providers_with(disposable_database, "after-swallowed@example.org") == 0
    assert engine.pool.checkedout() == 0


def test_an_interrupt_rolls_back_and_returns_the_connection(disposable_database, engine):
    with pytest.raises(KeyboardInterrupt):
        write("interrupted@example.org", then=fail(KeyboardInterrupt()))

    assert providers_with(disposable_database, "interrupted@example.org") == 0
    assert engine.pool.checkedout() == 0


def test_a_failing_rollback_keeps_the_original_error(disposable_database, engine):
    def lose_the_connection_then_fail(conn):
        pid = conn.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
        with verified_connection(disposable_database) as admin:
            admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
            assert wait_until(lambda: not backend_exists(admin, pid))
        raise ValueError("the original error")

    with pytest.raises(ValueError, match="the original error"):
        write("lost-before-rollback@example.org", then=lose_the_connection_then_fail)

    assert providers_with(disposable_database, "lost-before-rollback@example.org") == 0
    assert engine.pool.checkedout() == 0
    write("after-lost-rollback@example.org")  # the pool recovers
    assert providers_with(disposable_database, "after-lost-rollback@example.org") == 1


def test_a_savepoint_inside_the_block_works_as_usual(disposable_database, engine):
    def savepoint(conn):
        nested = conn.begin_nested()
        conn.execute(INSERT_PROVIDER, {"email": "inside-savepoint@example.org"})
        nested.rollback()

    write("outside-savepoint@example.org", then=savepoint)

    assert providers_with(disposable_database, "outside-savepoint@example.org") == 1
    assert providers_with(disposable_database, "inside-savepoint@example.org") == 0


def test_commit_and_rollback_outside_write_transaction_are_unaffected(disposable_database, engine):
    with engine.connect() as conn:  # ordinary SQLAlchemy use keeps working
        conn.execute(INSERT_PROVIDER, {"email": "plain-commit@example.org"})
        conn.commit()
        conn.execute(INSERT_PROVIDER, {"email": "plain-rollback@example.org"})
        conn.rollback()

    assert providers_with(disposable_database, "plain-commit@example.org") == 1
    assert providers_with(disposable_database, "plain-rollback@example.org") == 0


@pytest.mark.parametrize(
    "bypass",
    [
        lambda conn: conn.connection.driver_connection.commit(),
        lambda conn: conn.exec_driver_sql("COMMIT"),
    ],
    ids=["raw-driver-commit", "commit-as-sql"],
)
def test_a_commit_behind_sqlalchemys_back_is_detected_not_prevented(
    disposable_database, engine, bypass
):
    def bypass_then_insert(conn):
        bypass(conn)
        conn.execute(INSERT_PROVIDER, {"email": "after-bypass@example.org"})

    with pytest.raises(db_engine.TransactionOwnershipError, match="outside SQLAlchemy"):
        write("before-bypass@example.org", then=bypass_then_insert)

    # Unsupported: the bypass committed what came before it, and that stays committed.
    assert providers_with(disposable_database, "before-bypass@example.org") == 1
    assert providers_with(disposable_database, "after-bypass@example.org") == 0
    assert engine.pool.checkedout() == 0


def test_a_read_only_connection_does_not_leak_into_the_next_write(disposable_database, engine):
    with db_engine.read_only_connection() as conn:  # the same single pooled connection
        assert conn.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"

    write("after-read@example.org")

    assert providers_with(disposable_database, "after-read@example.org") == 1


def sleep_past_the_timeout(conn):
    conn.exec_driver_sql("SELECT pg_sleep(5)")


def test_the_statement_timeout_cancels_a_write_and_the_pool_recovers(disposable_database, engine):
    with pytest.raises(sa_exc.OperationalError) as caught:
        write("slow@example.org", then=sleep_past_the_timeout)

    assert isinstance(caught.value.orig, errors.QueryCanceled)
    assert sqlstate(caught.value) == "57014"
    assert providers_with(disposable_database, "slow@example.org") == 0
    write("after-timeout@example.org")  # the one pooled connection is usable again
    assert providers_with(disposable_database, "after-timeout@example.org") == 1


def test_the_lock_timeout_stops_a_write_waiting_for_a_lock(disposable_database, engine):
    with verified_connection(disposable_database) as holder:
        holder.autocommit = False
        # The holder's own server-side bound ends it if nothing else does (not the engine).
        holder.execute("SET idle_in_transaction_session_timeout = '15s'")
        holder.execute("LOCK TABLE providers IN ACCESS EXCLUSIVE MODE")
        started = time.monotonic()
        with pytest.raises(sa_exc.OperationalError) as caught:
            write("locked@example.org")
        waited = time.monotonic() - started
        holder.rollback()

    assert isinstance(caught.value.orig, errors.LockNotAvailable)
    assert waited < 10
    assert engine.pool.checkedout() == 0


def test_a_lost_connection_is_replaced_for_the_next_write(disposable_database, engine):
    with db_engine.write_transaction() as conn:
        pid = conn.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
    with verified_connection(disposable_database) as admin:
        admin.execute("SELECT pg_terminate_backend(%s)", (pid,))
        assert wait_until(lambda: not backend_exists(admin, pid))  # gone, not just signalled

    with db_engine.write_transaction() as conn:  # pre-ping replaces the dead connection
        conn.execute(INSERT_PROVIDER, {"email": "after-loss@example.org"})
        new_pid = conn.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
    assert new_pid != pid
    assert providers_with(disposable_database, "after-loss@example.org") == 1


# --- wrapped errors from real writes ---------------------------------------------------------


def translated(statement_or_emails, mapping):
    """Runs a write; a wrapped integrity error goes through raise_for_constraint."""
    try:
        if isinstance(statement_or_emails, str):
            with db_engine.write_transaction() as conn:
                conn.exec_driver_sql(statement_or_emails)
        else:
            write(*statement_or_emails)
    except sa_exc.IntegrityError as exc:
        raise_for_constraint(exc, mapping)


def test_a_real_duplicate_maps_to_the_existing_409(disposable_database, engine):
    write("duplicate@example.org")

    with pytest.raises(HTTPException) as caught:
        translated(["duplicate@example.org"], PROVIDER_WRITE_CONSTRAINTS)

    assert isinstance(caught.value.__cause__.orig, errors.UniqueViolation)
    assert (caught.value.status_code, caught.value.detail) == (
        409,
        "A provider with this email already exists.",
    )
    assert providers_with(disposable_database, "duplicate@example.org") == 1


MISSING_REFERENCES = (
    "INSERT INTO appointments (patient_id, provider_id, starts_at, ends_at) "
    "VALUES (999999, 999999, '2030-01-01 09:00+00', '2030-01-01 10:00+00')"
)


def test_a_real_missing_reference_maps_to_the_existing_404(engine):
    with pytest.raises(HTTPException) as caught:
        translated(MISSING_REFERENCES, APPOINTMENT_REFERENCE_CONSTRAINTS)

    assert caught.value.status_code == 404
    assert caught.value.detail in {"Patient not found", "Provider not found"}


BLANK_NAME = (
    "INSERT INTO providers (first_name, last_name, specialty) "
    "VALUES (' ', 'Synthetica', 'Synthetic Medicine')"
)


def test_a_real_violation_of_an_unmapped_constraint_is_reraised(engine):
    with pytest.raises(sa_exc.IntegrityError) as caught:
        translated(BLANK_NAME, PROVIDER_WRITE_CONSTRAINTS)

    assert violated_constraint(caught.value) == "providers_first_name_not_blank"


def test_wrapped_errors_hide_bound_parameters_but_not_the_server_detail(engine):
    write(SECRET)
    with pytest.raises(sa_exc.IntegrityError) as caught:
        write(SECRET)

    message = str(caught.value)
    assert "[SQL parameters hidden due to hide_parameters=True]" in message
    assert repr(caught.value.params) not in message  # the values stay out of the text
    # PostgreSQL's own DETAIL ("Key (email)=(...) already exists") still names the value.
    # It is never sent to clients (mapped errors carry only their fixed detail, unknown
    # ones become a plain 500), but it can reach server logs: a documented limitation.
    assert SECRET in str(caught.value.orig)
