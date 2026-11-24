"""
libpq environment variables and where test connections really go.

Every test connection passes host, port, user, password and dbname explicitly, but
libpq still reads some variables from the environment: PGHOSTADDR sends a connection
to another address while the host still reads as DB_HOST, PGSERVICE loads settings
from a service file, and PGOPTIONS sets session parameters. The harness therefore
refuses these when inherited (tests/db_safety.check_libpq_environment), and its own
privileged connections check where they actually arrived
(db_safety.check_destination) before running anything.

No test here connects anywhere except this session's disposable database and its
server. Redirection is shown at the driver boundary (libpq's connection defaults) and
with fake connections.
"""

import dataclasses
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg import pq

import db_engine
from database import get_connection
from tests import db_safety
from tests.disposable_database import DisposableDatabase

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "198.51.100.77"  # documentation address; never echoed by the environment check
VALID = {
    "MEDIBRIDGE_ENV": "test",
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_USER": "placeholder_user",
    "DB_PASSWORD": "placeholder-password",
    "JWT_SECRET_KEY": "x" * 64,
}
# The contract, stated here rather than read from the implementation.
REFUSED = ["PGHOSTADDR", "PGSERVICE", "PGOPTIONS"]


def libpq_defaults() -> dict[str, str | None]:
    """libpq's connection defaults, which include what it reads from the environment."""
    return {
        option.keyword.decode(): option.val.decode() if option.val is not None else None
        for option in pq.Conninfo.get_defaults()
    }


# --- why the guard exists: libpq picks these up (driver boundary, no connection) ---------


def test_libpq_takes_hostaddr_and_session_options_from_the_environment(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", SENTINEL)
    monkeypatch.setenv("PGOPTIONS", "-c search_path=pg_temp")

    defaults = libpq_defaults()

    assert defaults["hostaddr"] == SENTINEL
    assert defaults["options"] == "-c search_path=pg_temp"


def test_a_named_service_supplies_hostaddr_and_options(monkeypatch, tmp_path):
    service_file = tmp_path / "pg_service.conf"
    service_file.write_text(
        f"[synthetic]\nhostaddr={SENTINEL}\noptions=-c search_path=pg_temp\n", encoding="utf-8"
    )
    monkeypatch.setenv("PGSERVICEFILE", str(service_file))

    assert libpq_defaults()["hostaddr"] is None  # the file alone is not read

    monkeypatch.setenv("PGSERVICE", "synthetic")
    defaults = libpq_defaults()
    assert (defaults["hostaddr"], defaults["options"]) == (SENTINEL, "-c search_path=pg_temp")


# --- the startup contract ------------------------------------------------------------------


@pytest.mark.parametrize("name", REFUSED)
@pytest.mark.parametrize("value", [SENTINEL, ""], ids=["set", "empty"])
def test_inherited_redirecting_variables_are_refused_by_name_only(name, value):
    with pytest.raises(db_safety.UnsafeTestDatabase) as raised:
        db_safety.check_libpq_environment({name: value})

    message = str(raised.value)
    assert message.startswith(f"Unset {name} before running the tests")
    assert SENTINEL not in message


def test_all_refused_variables_are_named_together():
    with pytest.raises(db_safety.UnsafeTestDatabase) as raised:
        db_safety.check_libpq_environment(dict.fromkeys(REFUSED, SENTINEL))

    assert str(raised.value).startswith(f"Unset {', '.join(REFUSED)} before running")


@pytest.mark.parametrize("file_values", [None, VALID], ids=["no-test-env-file", "test-env-file"])
def test_settings_are_refused_before_they_are_resolved(file_values):
    # The check runs inside resolve_settings, which pytest_configure calls before the
    # first connection; a test env file does not exempt the shell.
    with pytest.raises(db_safety.UnsafeTestDatabase, match="Unset PGHOSTADDR"):
        db_safety.resolve_settings(file_values, {**VALID, "PGHOSTADDR": SENTINEL})


@pytest.mark.parametrize(
    "name",
    [
        "PGHOST",
        "PGPORT",
        "PGDATABASE",
        "PGUSER",
        "PGPASSWORD",
        "PGPASSFILE",
        "PGSERVICEFILE",
        "PGSYSCONFDIR",
        "PGSSLMODE",
        "PGCONNECT_TIMEOUT",
        "PGTZ",
        "PGAPPNAME",
    ],
)
def test_variables_that_cannot_redirect_or_reconfigure_sessions_are_accepted(name):
    db_safety.check_libpq_environment({name: "synthetic"})
    assert db_safety.resolve_settings(None, {**VALID, name: "synthetic"})["DB_HOST"] == "localhost"


COLLECT_ONLY_RUN = [
    sys.executable,
    "-m",
    "pytest",
    "--collect-only",
    "-q",
    "-p",
    "no:cacheprovider",
]


@pytest.mark.parametrize("name", REFUSED)
def test_a_test_run_with_an_inherited_variable_stops_before_any_database_work(name):
    # A fresh pytest run with the variable inherited. Were it not refused, PGHOSTADDR
    # 127.0.0.2 would reach the same local server (loopback), so nothing unsafe can happen
    # even if this test fails; PGCONNECT_TIMEOUT bounds any attempt.
    env = {
        key: value
        for key, value in os.environ.items()
        # The child run must not pick up pytest or coverage options meant for this run.
        if key != "PYTEST_ADDOPTS" and not key.startswith(("COV_", "COVERAGE_"))
    }
    env[name] = "127.0.0.2" if name == "PGHOSTADDR" else "synthetic"
    env["PGCONNECT_TIMEOUT"] = "5"
    result = subprocess.run(  # noqa: S603 - fixed arguments, no shell
        [*COLLECT_ONLY_RUN, "tests/test_main.py"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == pytest.ExitCode.USAGE_ERROR, output
    assert f"Unsafe test database configuration: Unset {name}" in output


# --- destination checks (fake connections) -----------------------------------------------


def info(hostaddr, host="localhost"):
    # Only hostaddr reflects where a connection went; host and port echo the settings.
    return SimpleNamespace(hostaddr=hostaddr, host=host)


@pytest.mark.parametrize(
    ("host", "reached"),
    [
        ("localhost", info("::1")),
        ("localhost", info("127.0.0.1")),
        ("LOCALHOST", info("127.0.0.1")),
        ("127.0.0.1", info("127.0.0.1")),
        ("::1", info("::1")),
        ("::1", info("0:0:0:0:0:0:0:1")),
        ("/var/run/postgresql", info("")),
        ("@medibridge", info("")),
        ("C:/pg/sockets", info("")),
        (r"\pg\sockets", info("")),
        ("db.test.internal", info("10.0.0.5")),
    ],
)
def test_connections_that_reached_the_configured_server_are_accepted(host, reached):
    db_safety.check_destination(reached, host)


@pytest.mark.parametrize(
    ("host", "reached"),
    [
        ("localhost", info(SENTINEL)),  # PGHOSTADDR-style redirect, host still "localhost"
        ("127.0.0.1", info("127.0.0.2")),
        ("::1", info("127.0.0.1")),
        ("/var/run/postgresql", info("127.0.0.1")),  # e.g. a service hostaddr on a socket host
        ("localhost", info("")),  # a socket connection when TCP was configured
        ("db.test.internal", info("")),
        ("localhost", info("not-an-address")),
        ("localhost", info(None)),
    ],
)
def test_connections_that_went_elsewhere_are_refused(host, reached):
    with pytest.raises(db_safety.UnsafeTestDatabase, match=r"^The connection reached "):
        db_safety.check_destination(reached, host)


class FakeConnection:
    def __init__(self, reached):
        self.info = reached
        self.closed = False
        self.statements = []

    def execute(self, statement, *args):
        self.statements.append(statement)
        raise AssertionError("no statement may run on an unchecked connection")

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def redirected(monkeypatch, test_database_config):
    """psycopg.connect, as the harness calls it, returns a connection that went elsewhere."""
    opened = []

    def connect(**kwargs):
        opened.append(FakeConnection(info(SENTINEL)))
        return opened[-1]

    monkeypatch.setattr("tests.disposable_database.psycopg.connect", connect)
    return opened


@pytest.mark.parametrize("step", ["role_status", "abandoned_databases", "create"])
def test_admin_steps_refuse_a_redirected_connection_before_any_statement(
    redirected, test_database_config, step
):
    database = DisposableDatabase(test_database_config, db_safety.new_database_name())

    with pytest.raises(db_safety.UnsafeTestDatabase, match="The connection reached"):
        getattr(database, step)()

    assert [conn.closed for conn in redirected] == [True]
    assert [conn.statements for conn in redirected] == [[]]
    assert not database.create_attempted


def test_drop_refuses_a_redirected_connection_and_drops_nothing(redirected, test_database_config):
    database = DisposableDatabase(test_database_config, db_safety.new_database_name())
    database.create_attempted = True  # as after a CREATE DATABASE on the real server

    with pytest.raises(db_safety.UnsafeTestDatabase):
        database.drop()

    assert not database.dropped
    assert database.drop_error.startswith("UnsafeTestDatabase: The connection reached")
    assert [conn.statements for conn in redirected] == [[]]


def test_cleanup_connection_refuses_a_redirected_connection(redirected, test_database_config):
    database = DisposableDatabase(test_database_config, db_safety.new_database_name())

    with pytest.raises(db_safety.UnsafeTestDatabase):
        database.open_cleanup_connection()

    assert database.cleanup_connection is None
    assert [conn.statements for conn in redirected] == [[]]


def test_a_redirected_ip_host_is_refused_even_on_loopback(monkeypatch, test_database_config):
    # Configured as 127.0.0.1: arriving at another loopback address is still refused.
    config = dataclasses.replace(test_database_config, host="127.0.0.1")
    opened = []

    def connect(**kwargs):
        opened.append(FakeConnection(info("127.0.0.2", host="127.0.0.1")))
        return opened[-1]

    monkeypatch.setattr("tests.disposable_database.psycopg.connect", connect)

    with pytest.raises(db_safety.UnsafeTestDatabase, match=r"127\.0\.0\.2"):
        DisposableDatabase(config, db_safety.new_database_name()).admin_connection()


# --- real connections in this session --------------------------------------------------


def test_the_application_connections_in_this_session_reach_the_configured_server(
    disposable_database,
):
    # The harness's own connections are checked in DisposableDatabase._connect. The
    # application's two paths are not checked at run time (they rely on the startup
    # refusal); this shows that, as configured for this run, both reach the right place.
    host = disposable_database.config.host
    with get_connection() as legacy:
        db_safety.check_destination(legacy.info, host)
        assert legacy.info.dbname == disposable_database.name
    with db_engine.get_engine().connect() as conn:
        pooled = conn.connection.driver_connection
        db_safety.check_destination(pooled.info, host)
        assert pooled.info.dbname == disposable_database.name


def test_a_test_owned_pgoptions_still_applies_to_new_connections(monkeypatch, disposable_database):
    # What tests/test_pg_concurrency.py relies on: PGOPTIONS set by the test itself.
    monkeypatch.setenv("PGOPTIONS", "-c lock_timeout=1234ms")
    kwargs = disposable_database.config.connect_kwargs(disposable_database.name)
    with psycopg.connect(**kwargs) as conn:
        assert conn.execute("SHOW lock_timeout").fetchone()[0] == "1234ms"
