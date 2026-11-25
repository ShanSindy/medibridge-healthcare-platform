"""
The shared SQLAlchemy engine (db_engine): settings, pool limits and connection lifecycle.

Configuration tests build engines without connecting. Pool and lifecycle tests use
engines of their own with small limits, all pointed at this session's disposable
database (DB_NAME is set to it for the whole run) and verified to be connected to it
before use; each is disposed after the test. The only connection terminated on purpose
is one of the test's own pooled connections, after checking that it belongs to this
session's database.

Waits are bounded: the pool timeout is 1-3 seconds, barriers and joins have explicit
timeouts, and `wait_until` polls a condition for at most WAIT_SECONDS. Tests that hold a
table lock also bound the holder itself (idle_in_transaction_session_timeout), so the
server ends it and releases the lock even if the engine's timeout policy were broken.
"""

import logging
import os
import secrets
import threading
import time

import psycopg
import pytest
from sqlalchemy import QueuePool, make_url, text
from sqlalchemy import exc as sa_exc

import db_engine
from tests import db_safety
from tests.pg_helpers import backend_exists, verified_connection, verify_identity, wait_until

WAIT_SECONDS = 10
SETTINGS = {
    "DB_HOST": "db.example.internal",
    "DB_PORT": "5432",
    "DB_NAME": "medibridge_app",
    "DB_USER": "medibridge_app",
    "DB_PASSWORD": "synthetic-password-not-a-secret",
}


# --- settings (no connections) ----------------------------------------------------------


def test_url_is_built_from_the_settings():
    url = db_engine.database_url(SETTINGS)

    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database, url.username, url.password) == (
        "db.example.internal",
        5432,
        "medibridge_app",
        "medibridge_app",
        "synthetic-password-not-a-secret",
    )


def test_the_password_is_masked_in_the_url_and_the_engine_representation():
    engine = db_engine.build_engine(SETTINGS)
    try:
        for rendered in (str(engine.url), repr(engine.url), repr(engine)):
            assert SETTINGS["DB_PASSWORD"] not in rendered
    finally:
        engine.dispose()


def test_connection_arguments_reach_the_driver_unchanged():
    # A Unix-socket directory as the host, and a password full of URL and libpq syntax.
    password = "p@ss:/w?rd#x%41 dbname=other host='evil'"
    settings = {**SETTINGS, "DB_HOST": "/var/run/postgresql", "DB_PASSWORD": password}
    engine = db_engine.build_engine(settings)
    try:
        _, kwargs = engine.dialect.create_connect_args(engine.url)
    finally:
        engine.dispose()

    assert kwargs["host"] == "/var/run/postgresql"
    assert kwargs["password"] == password
    assert (kwargs["dbname"], kwargs["user"], kwargs["port"]) == (
        "medibridge_app",
        "medibridge_app",
        5432,
    )


def test_credentials_are_never_parsed_as_url_syntax():
    tricky = {**SETTINGS, "DB_USER": "app@other-host", "DB_PASSWORD": "p@ss:/w?rd#x%41"}

    url = db_engine.database_url(tricky)
    round_trip = make_url(url.render_as_string(hide_password=False))

    assert (url.username, url.password, url.host) == (
        "app@other-host",
        "p@ss:/w?rd#x%41",
        "db.example.internal",
    )
    assert (round_trip.username, round_trip.password, round_trip.host) == (
        url.username,
        url.password,
        url.host,
    )


@pytest.mark.parametrize(
    "missing",
    [("DB_HOST",), ("DB_PORT",), ("DB_NAME",), ("DB_USER",), ("DB_HOST", "DB_NAME")],
)
@pytest.mark.parametrize("how", ["absent", "empty", "blank"])
def test_missing_settings_are_named_without_values(missing, how):
    settings = dict(SETTINGS)
    for name in missing:
        if how == "absent":
            del settings[name]
        else:
            settings[name] = "" if how == "empty" else "   "

    with pytest.raises(db_engine.DatabaseConfigurationError) as raised:
        db_engine.database_url(settings)

    message = str(raised.value)
    assert message == f"Missing database settings: {', '.join(missing)}."
    for value in SETTINGS.values():
        assert value not in message


def test_the_password_is_optional():
    settings = {name: value for name, value in SETTINGS.items() if name != "DB_PASSWORD"}

    assert db_engine.database_url(settings).password is None
    assert db_engine.database_url({**settings, "DB_PASSWORD": ""}).password is None


@pytest.mark.parametrize(
    "port", ["abc", "5432x", "0", "65536", "-1", "5_432", "+5432", "\uff15\uff14\uff13\uff12"]
)
def test_an_invalid_port_is_rejected_without_echoing_it(port):
    with pytest.raises(db_engine.DatabaseConfigurationError) as raised:
        db_engine.database_url({**SETTINGS, "DB_PORT": port})

    assert "DB_PORT" in str(raised.value)
    assert port not in str(raised.value)


def test_pool_defaults():
    assert db_engine.pool_settings({}) == db_engine.PoolSettings(
        size=5, max_overflow=5, timeout_seconds=10, connect_timeout_seconds=10
    )


def test_pool_settings_are_read_and_blank_values_mean_the_default():
    settings = {
        "DB_POOL_SIZE": "2",
        "DB_POOL_MAX_OVERFLOW": "0",
        "DB_POOL_TIMEOUT_SECONDS": " 3 ",
        "DB_CONNECT_TIMEOUT_SECONDS": "",
    }

    assert db_engine.pool_settings(settings) == db_engine.PoolSettings(
        size=2, max_overflow=0, timeout_seconds=3, connect_timeout_seconds=10
    )


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("DB_POOL_SIZE", "0", "DB_POOL_SIZE must be between 1 and 50."),
        ("DB_POOL_SIZE", "51", "DB_POOL_SIZE must be between 1 and 50."),
        ("DB_POOL_SIZE", "2.5", "DB_POOL_SIZE must be a whole number."),
        ("DB_POOL_SIZE", "1_0", "DB_POOL_SIZE must be a whole number."),
        ("DB_POOL_MAX_OVERFLOW", "-1", "DB_POOL_MAX_OVERFLOW must be between 0 and 50."),
        ("DB_POOL_MAX_OVERFLOW", "51", "DB_POOL_MAX_OVERFLOW must be between 0 and 50."),
        ("DB_POOL_TIMEOUT_SECONDS", "0", "DB_POOL_TIMEOUT_SECONDS must be between 1 and 300."),
        ("DB_POOL_TIMEOUT_SECONDS", "301", "DB_POOL_TIMEOUT_SECONDS must be between 1 and 300."),
        (
            "DB_CONNECT_TIMEOUT_SECONDS",
            "0",
            "DB_CONNECT_TIMEOUT_SECONDS must be between 1 and 300.",
        ),
        ("DB_CONNECT_TIMEOUT_SECONDS", "ten", "DB_CONNECT_TIMEOUT_SECONDS must be a whole number."),
    ],
)
def test_pool_settings_out_of_range_are_rejected(name, value, message):
    with pytest.raises(db_engine.DatabaseConfigurationError) as raised:
        db_engine.pool_settings({name: value})

    assert str(raised.value) == message


def test_transaction_timeout_defaults():
    assert db_engine.transaction_timeouts({}) == db_engine.TransactionTimeouts(
        statement_seconds=30, lock_seconds=5
    )


def test_transaction_timeouts_are_read():
    settings = {"DB_STATEMENT_TIMEOUT_SECONDS": "12", "DB_LOCK_TIMEOUT_SECONDS": " 2 "}

    assert db_engine.transaction_timeouts(settings) == db_engine.TransactionTimeouts(
        statement_seconds=12, lock_seconds=2
    )


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        # 0 would mean "no limit" in PostgreSQL.
        ("DB_STATEMENT_TIMEOUT_SECONDS", "0", "between 1 and 300"),
        ("DB_STATEMENT_TIMEOUT_SECONDS", "301", "between 1 and 300"),
        ("DB_STATEMENT_TIMEOUT_SECONDS", "30s", "a whole number"),
        ("DB_LOCK_TIMEOUT_SECONDS", "0", "between 1 and 60"),
        ("DB_LOCK_TIMEOUT_SECONDS", "61", "between 1 and 60"),
        ("DB_LOCK_TIMEOUT_SECONDS", "-5", "between 1 and 60"),
    ],
)
def test_transaction_timeouts_out_of_range_are_rejected(name, value, message):
    with pytest.raises(db_engine.DatabaseConfigurationError, match=f"^{name} must be {message}"):
        db_engine.transaction_timeouts({name: value})


def test_build_engine_applies_the_pool_settings_without_connecting():
    engine = db_engine.build_engine(
        {
            **SETTINGS,
            "DB_POOL_SIZE": "2",
            "DB_POOL_MAX_OVERFLOW": "1",
            "DB_POOL_TIMEOUT_SECONDS": "3",
        }
    )
    try:
        assert isinstance(engine.pool, QueuePool)
        assert (engine.pool.size(), engine.pool.timeout()) == (2, 3)
        assert engine.pool.checkedin() == engine.pool.checkedout() == 0
    finally:
        engine.dispose()


# --- the process-wide engine (no connections) --------------------------------------------


class FakeEngine:
    def __init__(self):
        self.disposed = 0

    def dispose(self):
        self.disposed += 1


@pytest.fixture
def fresh_singleton(monkeypatch):
    """Runs a test with no process-wide engine and a counting fake builder; restores both."""
    built = []

    def build(environ):
        built.append(FakeEngine())
        return built[-1]

    monkeypatch.setattr(db_engine, "_engine", None)
    monkeypatch.setattr(db_engine, "build_engine", build)
    return built


def test_concurrent_first_use_builds_one_engine(fresh_singleton, monkeypatch):
    threads_count = 8
    barrier = threading.Barrier(threads_count, timeout=WAIT_SECONDS)
    results = []
    build = db_engine.build_engine

    def slow_build(environ):
        # Keeps the first build in progress while the other threads arrive, so that
        # unsynchronised first use would build several engines. The pass condition does
        # not depend on this timing.
        time.sleep(0.2)
        return build(environ)

    monkeypatch.setattr(db_engine, "build_engine", slow_build)

    def first_use():
        barrier.wait()
        results.append(db_engine.get_engine())

    threads = [threading.Thread(target=first_use) for _ in range(threads_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT_SECONDS)

    assert not any(thread.is_alive() for thread in threads)
    assert len(fresh_singleton) == 1
    assert results == [fresh_singleton[0]] * threads_count


def test_dispose_engine_disposes_and_forgets_the_engine(fresh_singleton):
    first = db_engine.get_engine()

    db_engine.dispose_engine()
    db_engine.dispose_engine()  # nothing to dispose: no error, no second dispose

    assert first.disposed == 1
    second = db_engine.get_engine()
    assert second is not first
    assert len(fresh_singleton) == 2


# --- connections to this session's disposable database ------------------------------------


@pytest.fixture
def direct(disposable_database):
    """An autocommit psycopg connection, verified to be this session's database."""
    with verified_connection(disposable_database) as conn:
        yield conn


@pytest.fixture
def small_engine(disposable_database):
    """Builds engines for this session's database with the given pool limits; disposes them."""
    engines = []

    def build(size, max_overflow=0, timeout_seconds=3, **settings):
        engine = db_engine.build_engine(
            {
                **os.environ,
                "DB_POOL_SIZE": str(size),
                "DB_POOL_MAX_OVERFLOW": str(max_overflow),
                "DB_POOL_TIMEOUT_SECONDS": str(timeout_seconds),
                **settings,
            }
        )
        engines.append(engine)
        assert engine.url.database == disposable_database.name
        with engine.connect() as conn:
            verify_identity(conn.connection.driver_connection, disposable_database)
        return engine

    yield build
    for engine in engines:
        engine.dispose()


def backend_pid(conn) -> int:
    return conn.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()


def audit_rows(direct) -> int:
    return direct.execute("SELECT count(*) FROM audit_logs").fetchone()[0]


INSERT_EVENT = (
    "INSERT INTO audit_logs (action, resource_type, outcome) "
    "VALUES ('engine.test', 'synthetic', 'success')"
)


def test_the_application_engine_targets_this_sessions_disposable_database(disposable_database):
    engine = db_engine.get_engine()

    assert engine.url.database == disposable_database.name
    assert engine.url.host == disposable_database.config.host
    with engine.connect() as conn:
        raw = conn.connection.driver_connection
        verify_identity(raw, disposable_database)
        # The configured connect timeout reaches libpq (10 unless the test settings set one).
        expected = db_engine.pool_settings(os.environ).connect_timeout_seconds
        assert raw.info.get_parameters()["connect_timeout"] == str(expected)


def test_a_returned_connection_is_reused(small_engine):
    engine = small_engine(size=1)

    with engine.connect() as conn:
        first = backend_pid(conn)
    with engine.connect() as conn:
        second = backend_pid(conn)

    assert first == second
    assert engine.pool.checkedout() == 0


def test_an_exhausted_pool_times_out_and_recovers_when_a_connection_returns(small_engine):
    engine = small_engine(size=1, timeout_seconds=1)

    with engine.connect() as held:
        held_pid = backend_pid(held)
        started = time.monotonic()
        with pytest.raises(sa_exc.TimeoutError):
            engine.connect()
        waited = time.monotonic() - started

    assert 0.9 <= waited < WAIT_SECONDS
    with engine.connect() as conn:
        assert backend_pid(conn) == held_pid


def test_concurrent_checkouts_get_distinct_connections_up_to_the_limit(small_engine):
    # size 2 + overflow 1: three workers hold three different connections at once, and a
    # fourth checkout times out while they do.
    engine = small_engine(size=2, max_overflow=1, timeout_seconds=1)
    workers = 3
    all_holding = threading.Barrier(workers + 1, timeout=WAIT_SECONDS)
    release = threading.Event()
    pids, errors = [], []

    def hold():
        try:
            with engine.connect() as conn:
                pids.append(backend_pid(conn))
                all_holding.wait()
                if not release.wait(WAIT_SECONDS):
                    raise TimeoutError("release was not signalled")
        except BaseException as exc:
            errors.append(exc)
            all_holding.abort()

    threads = [threading.Thread(target=hold) for _ in range(workers)]
    for thread in threads:
        thread.start()
    try:
        all_holding.wait()
        assert engine.pool.checkedout() == workers
        with pytest.raises(sa_exc.TimeoutError):
            engine.connect()
    finally:
        release.set()
        for thread in threads:
            thread.join(WAIT_SECONDS)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(set(pids)) == workers
    assert engine.pool.checkedout() == 0


def test_a_failed_statement_propagates_and_does_not_poison_the_pooled_connection(small_engine):
    engine = small_engine(size=1)

    with engine.connect() as conn:
        failed_pid = backend_pid(conn)
        with pytest.raises(sa_exc.DataError) as raised:
            conn.exec_driver_sql("SELECT 1 / 0")
        # The error leaves the transaction aborted; the block ends without handling it.

    assert isinstance(raised.value.orig, psycopg.errors.DivisionByZero)
    with engine.connect() as conn:
        assert backend_pid(conn) == failed_pid
        assert conn.exec_driver_sql("SELECT 1").scalar_one() == 1


def test_statement_parameters_are_left_out_of_error_messages(small_engine):
    engine = small_engine(size=1)
    sentinel = "Synthetic Patient Rowan Ledger"

    with engine.connect() as conn, pytest.raises(sa_exc.DataError) as raised:
        # The server's own message ("division by zero") does not echo the parameter.
        conn.execute(text("SELECT CAST(:name AS text), 1 / 0"), {"name": sentinel})

    message = str(raised.value)
    assert "SQL parameters hidden" in message
    assert sentinel not in message


def test_statement_parameters_are_left_out_of_sql_logs(small_engine, caplog):
    engine = small_engine(size=1)
    sentinel = "Synthetic Patient Rowan Ledger"
    caplog.set_level(logging.INFO, logger="sqlalchemy.engine")

    with engine.connect() as conn:
        conn.execute(text("SELECT CAST(:name AS text)"), {"name": sentinel})

    assert "SELECT CAST(%(name)s AS text)" in caplog.text  # statements are logged...
    assert "SQL parameters hidden" in caplog.text
    assert sentinel not in caplog.text  # ...their parameters are not


def test_pghost_pgport_and_pgdatabase_cannot_redirect_the_engine(
    small_engine, monkeypatch, disposable_database
):
    # Explicit settings win over these libpq variables. PGHOSTADDR, PGSERVICE and
    # PGOPTIONS would still apply, so the test harness refuses them at startup
    # (tests/test_libpq_environment.py); outside tests they apply to both paths.
    monkeypatch.setenv("PGDATABASE", "postgres")
    monkeypatch.setenv("PGPORT", "9")
    monkeypatch.setenv("PGHOST", "invalid.example")
    engine = small_engine(size=1)  # connects and verifies this session's database

    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT current_database()").scalar_one() == (
            disposable_database.name
        )


def test_connect_blocks_never_commit(small_engine, direct):
    engine = small_engine(size=1)

    with engine.connect() as conn:
        conn.exec_driver_sql(INSERT_EVENT)

    assert audit_rows(direct) == 0


def test_begin_blocks_commit_on_success_and_roll_back_on_error(small_engine, direct):
    engine = small_engine(size=1)

    class Abort(Exception):
        pass

    def insert_then_fail():
        with engine.begin() as conn:
            conn.exec_driver_sql(INSERT_EVENT)
            raise Abort

    with pytest.raises(Abort):
        insert_then_fail()
    assert audit_rows(direct) == 0

    with engine.begin() as conn:
        conn.exec_driver_sql(INSERT_EVENT)
    assert audit_rows(direct) == 1


def test_a_connection_closed_by_the_server_is_replaced_before_use(
    small_engine, direct, disposable_database
):
    engine = small_engine(size=1)
    with engine.connect() as conn:
        old_pid = backend_pid(conn)

    # Terminate only our own idle pooled connection, after checking where it is connected.
    database = direct.execute(
        "SELECT datname FROM pg_stat_activity WHERE pid = %s AND usename = current_user",
        (old_pid,),
    ).fetchone()
    assert database == (disposable_database.name,)
    assert direct.execute("SELECT pg_terminate_backend(%s)", (old_pid,)).fetchone() == (True,)
    assert wait_until(lambda: not backend_exists(direct, old_pid))

    with engine.connect() as conn:
        assert backend_pid(conn) != old_pid
        assert conn.exec_driver_sql("SELECT 1").scalar_one() == 1


def test_dispose_closes_idle_pooled_connections(small_engine, direct):
    engine = small_engine(size=1)
    with engine.connect() as conn:
        pid = backend_pid(conn)
    assert backend_exists(direct, pid)

    engine.dispose()

    assert wait_until(lambda: not backend_exists(direct, pid))


def test_a_database_that_does_not_exist_fails_without_revealing_the_password():
    # A random, never-created name; connecting to it changes nothing on the server.
    missing = f"{db_safety.DATABASE_PREFIX}{secrets.token_hex(8)}"
    engine = db_engine.build_engine({**os.environ, "DB_NAME": missing})
    try:
        with pytest.raises(sa_exc.OperationalError) as raised:
            engine.connect()
    finally:
        engine.dispose()

    assert missing in str(raised.value)
    password = os.environ.get("DB_PASSWORD")
    if password:
        assert password not in str(raised.value)


def test_an_unreachable_server_fails_with_an_operational_error_in_bounded_time():
    # On Linux a refused connection fails at once; on Windows libpq retries until the
    # connect timeout. Either way the call must end. That the timeout setting reaches
    # libpq is checked on a live connection in the targeting test above.
    engine = db_engine.build_engine(
        {
            **os.environ,
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "9",  # nothing listens here
            "DB_CONNECT_TIMEOUT_SECONDS": "2",
        }
    )
    started = time.monotonic()
    try:
        with pytest.raises(sa_exc.OperationalError):
            engine.connect()
    finally:
        engine.dispose()

    assert time.monotonic() - started < WAIT_SECONDS


# --- transaction timeouts and read-only connections --------------------------------------

TIMEOUTS = "SELECT current_setting('statement_timeout'), current_setting('lock_timeout')"


def lock_with_bounded_holder(holder) -> None:
    """
    Takes an exclusive lock on audit_logs in `holder`'s transaction. The holder's own
    idle_in_transaction_session_timeout makes the server end it, and so release the lock,
    after 15 s even if nothing else stops the waiting statement: the bound does not
    depend on the engine code under test.
    """
    holder.execute("SET idle_in_transaction_session_timeout = '15s'")
    holder.execute("LOCK TABLE audit_logs IN ACCESS EXCLUSIVE MODE")


def session_timeouts(engine) -> tuple[str, str]:
    """The timeouts on the next pooled connection outside any engine transaction."""
    raw = engine.raw_connection()  # bypasses Connection, so no "begin" event runs
    try:
        cursor = raw.cursor()
        cursor.execute(TIMEOUTS)
        values = cursor.fetchone()
        raw.rollback()
        return values
    finally:
        raw.close()


def test_every_engine_transaction_gets_the_default_timeouts(small_engine):
    engine = small_engine(size=1)

    with engine.connect() as conn:
        assert conn.exec_driver_sql(TIMEOUTS).one() == ("30s", "5s")
    with engine.begin() as conn:
        assert conn.exec_driver_sql(TIMEOUTS).one() == ("30s", "5s")


def test_configured_timeouts_apply(small_engine):
    engine = small_engine(size=1, DB_STATEMENT_TIMEOUT_SECONDS="7", DB_LOCK_TIMEOUT_SECONDS="2")

    with engine.connect() as conn:
        assert conn.exec_driver_sql(TIMEOUTS).one() == ("7s", "2s")


def test_timeouts_end_with_the_transaction(small_engine, direct):
    engine = small_engine(size=1)
    server_defaults = direct.execute(TIMEOUTS).fetchone()
    assert server_defaults != ("30s", "5s")

    with engine.connect() as conn:
        pid = backend_pid(conn)
        assert conn.exec_driver_sql(TIMEOUTS).one() == ("30s", "5s")

    raw = engine.raw_connection()
    try:
        assert raw.driver_connection.info.backend_pid == pid  # the same pooled connection
    finally:
        raw.close()
    assert session_timeouts(engine) == server_defaults

    # A rolled-back transaction would undo even a session-level SET, so the decisive case
    # is a committed one: the timeouts must still end with it.
    with engine.begin() as conn:
        assert conn.exec_driver_sql(TIMEOUTS).one() == ("30s", "5s")
    assert session_timeouts(engine) == server_defaults


def test_test_owned_session_settings_survive_engine_transactions(small_engine, monkeypatch):
    # PGOPTIONS set by a test (as the concurrency tests do) is a session setting. Being
    # stricter than the limits, it stays in force inside engine transactions, and it is
    # still in place afterwards.
    monkeypatch.setenv("PGOPTIONS", "-c statement_timeout=12345ms -c lock_timeout=2345ms")
    engine = small_engine(size=1)

    with engine.connect() as conn:
        assert conn.exec_driver_sql(TIMEOUTS).one() == ("12345ms", "2345ms")
    assert session_timeouts(engine) == ("12345ms", "2345ms")


def test_the_statement_timeout_cancels_a_long_statement(small_engine):
    engine = small_engine(size=1, DB_STATEMENT_TIMEOUT_SECONDS="1")

    started = time.monotonic()
    with engine.connect() as conn, pytest.raises(sa_exc.OperationalError) as raised:
        conn.exec_driver_sql("SELECT pg_sleep(5)")

    assert isinstance(raised.value.orig, psycopg.errors.QueryCanceled)
    assert time.monotonic() - started < 4
    with engine.connect() as conn:  # the connection is usable again
        assert conn.exec_driver_sql("SELECT 1").scalar_one() == 1


def test_the_lock_timeout_stops_waiting_for_a_locked_table(
    small_engine, disposable_database, monkeypatch
):
    # Backstop, so this test cannot hang if the policy ever stops being applied: a
    # session-level statement_timeout (the transaction-local one replaces it while the
    # policy works). The assertions below still require the lock timeout itself.
    monkeypatch.setenv("PGOPTIONS", "-c statement_timeout=10s")
    engine = small_engine(size=1, DB_LOCK_TIMEOUT_SECONDS="1", DB_STATEMENT_TIMEOUT_SECONDS="10")
    holder = psycopg.connect(**disposable_database.config.connect_kwargs(disposable_database.name))
    try:
        verify_identity(holder, disposable_database)
        assert session_timeouts(engine)[1] == "0"  # so only the configured 1 s can apply
        lock_with_bounded_holder(holder)

        started = time.monotonic()
        with engine.connect() as conn, pytest.raises(sa_exc.OperationalError) as raised:
            conn.exec_driver_sql("SELECT count(*) FROM audit_logs")
        waited = time.monotonic() - started
    finally:
        holder.close()  # ends its transaction and releases the lock

    assert isinstance(raised.value.orig, psycopg.errors.LockNotAvailable)
    assert waited < 4


def test_read_only_connection_refuses_writes_and_does_not_leak_read_only(
    small_engine, monkeypatch, direct
):
    engine = small_engine(size=1)
    monkeypatch.setattr(db_engine, "_engine", engine)

    with db_engine.read_only_connection() as conn:
        assert conn.exec_driver_sql("SHOW transaction_read_only").scalar_one() == "on"
        assert conn.exec_driver_sql(TIMEOUTS).one() == ("30s", "5s")
        with pytest.raises(sa_exc.InternalError) as raised:
            conn.exec_driver_sql(INSERT_EVENT)
    assert isinstance(raised.value.orig, psycopg.errors.ReadOnlySqlTransaction)

    with engine.begin() as conn:  # the same, only, pooled connection
        conn.exec_driver_sql(INSERT_EVENT)
    assert audit_rows(direct) == 1
    assert engine.pool.checkedout() == 0


def test_read_only_connection_returns_its_connection_when_the_block_fails(
    small_engine, monkeypatch
):
    engine = small_engine(size=1)
    monkeypatch.setattr(db_engine, "_engine", engine)

    class Failure(Exception):
        pass

    def read_then_fail():
        with db_engine.read_only_connection() as conn:
            conn.exec_driver_sql("SELECT 1")
            raise Failure

    with pytest.raises(Failure):
        read_then_fail()

    assert engine.pool.checkedout() == 0


# --- the timeout policy against existing session settings --------------------------------

# Session settings (as from PGOPTIONS or a role setting), and what an engine transaction
# with the default limits (30 s, 5 s) must use: the stricter positive value of the two.
SESSION_CASES = {
    "disabled": ("0", "0", ("30s", "5s")),
    "stricter": ("2s", "1s", ("2s", "1s")),
    "less-strict": ("60s", "20s", ("30s", "5s")),
    "equal": ("30s", "5s", ("30s", "5s")),
    "subsecond": ("500ms", "250ms", ("500ms", "250ms")),
    "minutes": ("1min", "1min", ("30s", "5s")),
    "statement-only-stricter": ("2s", "0", ("2s", "5s")),
    "lock-only-stricter": ("0", "1500ms", ("30s", "1500ms")),
}


@pytest.mark.parametrize(
    ("statement", "lock", "effective"), SESSION_CASES.values(), ids=SESSION_CASES.keys()
)
def test_the_stricter_of_the_limit_and_the_session_setting_applies(
    small_engine, monkeypatch, statement, lock, effective
):
    monkeypatch.setenv("PGOPTIONS", f"-c statement_timeout={statement} -c lock_timeout={lock}")
    engine = small_engine(size=1)
    session = session_timeouts(engine)

    with engine.connect() as conn:  # rolled back
        assert conn.exec_driver_sql(TIMEOUTS).one() == effective
    assert session_timeouts(engine) == session
    with engine.begin() as conn:  # committed
        assert conn.exec_driver_sql(TIMEOUTS).one() == effective
    assert session_timeouts(engine) == session


def test_timeout_settings_are_whole_milliseconds(direct):
    # The policy compares pg_settings.setting numerically; that relies on this unit.
    units = direct.execute(
        "SELECT name, unit, vartype FROM pg_settings "
        "WHERE name IN ('statement_timeout', 'lock_timeout') ORDER BY name"
    ).fetchall()
    assert units == [("lock_timeout", "ms", "integer"), ("statement_timeout", "ms", "integer")]


def test_a_stricter_session_statement_timeout_still_cancels(small_engine, monkeypatch):
    monkeypatch.setenv("PGOPTIONS", "-c statement_timeout=500ms")
    engine = small_engine(size=1)  # configured limit 30 s

    started = time.monotonic()
    with engine.connect() as conn, pytest.raises(sa_exc.OperationalError) as raised:
        conn.exec_driver_sql("SELECT pg_sleep(3)")

    assert isinstance(raised.value.orig, psycopg.errors.QueryCanceled)
    assert time.monotonic() - started < 2.5


def test_a_stricter_session_lock_timeout_still_stops_the_wait(
    small_engine, disposable_database, monkeypatch
):
    # Session lock_timeout 1 s, configured limit 5 s; statement_timeout 10 s as a backstop.
    monkeypatch.setenv("PGOPTIONS", "-c lock_timeout=1s -c statement_timeout=10s")
    engine = small_engine(size=1)
    holder = psycopg.connect(**disposable_database.config.connect_kwargs(disposable_database.name))
    try:
        verify_identity(holder, disposable_database)
        lock_with_bounded_holder(holder)

        started = time.monotonic()
        with engine.connect() as conn, pytest.raises(sa_exc.OperationalError) as raised:
            conn.exec_driver_sql("SELECT count(*) FROM audit_logs")
        waited = time.monotonic() - started
    finally:
        holder.close()  # ends its transaction and releases the lock

    assert isinstance(raised.value.orig, psycopg.errors.LockNotAvailable)
    assert waited < 4  # well below the 5 s configured limit


def test_a_failure_to_apply_the_timeouts_propagates_and_the_pool_recovers(
    small_engine, monkeypatch
):
    engine = small_engine(size=1)
    with engine.connect() as conn:
        pid = backend_pid(conn)

    def read():
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")

    with monkeypatch.context() as patch:
        # Fault injection: the listener's statement fails in PostgreSQL.
        patch.setattr(db_engine, "_APPLY_TIMEOUTS", "SELECT 1 / 0")
        with pytest.raises(sa_exc.DataError) as raised:
            read()
    assert isinstance(raised.value.orig, psycopg.errors.DivisionByZero)
    assert engine.pool.checkedout() == 0

    with engine.connect() as conn:  # the same pooled connection, usable and policed again
        assert backend_pid(conn) == pid
        assert conn.exec_driver_sql(TIMEOUTS).one() == ("30s", "5s")


@pytest.mark.parametrize(
    ("session", "effective"),
    [("10s", "7s"), ("2s", "2s"), ("0", "7s")],
    ids=["session-looser", "session-stricter", "session-disabled"],
)
def test_a_configured_limit_combines_with_the_session_value(
    small_engine, monkeypatch, session, effective
):
    monkeypatch.setenv("PGOPTIONS", f"-c statement_timeout={session}")
    engine = small_engine(size=1, DB_STATEMENT_TIMEOUT_SECONDS="7")

    with engine.connect() as conn:
        assert conn.exec_driver_sql(TIMEOUTS).one()[0] == effective


def test_the_current_session_value_counts_not_the_connection_default(small_engine):
    # A committed session-level SET changes the current value but not the connection's
    # reset value; the policy must use the current one.
    engine = small_engine(size=1)
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("SET statement_timeout = '2s'")
        raw.commit()
    finally:
        raw.close()

    with engine.connect() as conn:
        assert conn.exec_driver_sql(TIMEOUTS).one()[0] == "2s"
        reset = conn.exec_driver_sql(
            "SELECT reset_val FROM pg_settings WHERE name = 'statement_timeout'"
        ).scalar_one()
    assert reset != "2000"
