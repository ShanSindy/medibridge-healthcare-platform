"""
Database isolation for the MediBridge test suite.

Every pytest session gets its own disposable PostgreSQL database:

- Created on the configured test server with a cryptographically random name
  (`medibridge_test_<16 hex>`). Its OID and owner are recorded right after
  creation, it is marked with an ownership comment, and it is built with the
  project's own migrations (`migrate.py`).
- Emptied before and after every test. All application tables are truncated
  in one statement with CASCADE; `schema_migrations` is kept. Tables outside
  the `public` schema stop the run instead of being skipped.
- Dropped at the end of the session, only after re-proving its identity.

No existing database (development, `medibridge_test`, `medibridge_ci_test`)
is read, modified or dropped (see tests/db_safety.py and
tests/disposable_database.py).

Configuration comes from `.env.test` (see `.env.test.example`) or, when that
file is absent as in CI, from the process environment. Settings the file
omits are removed from the environment, and the application's own
`load_dotenv()` calls are disabled, so neither the development `.env` nor the
shell can supply a value. The superuser override is accepted only from
`.env.test`.

tests/conftest.py refuses to run if this file was not loaded, for example
when pytest is started from inside `tests/`.

Limitation: if the process is killed (for example with SIGKILL), its
database is left behind. Later runs report such databases but never delete
them.
"""

import os
from pathlib import Path

import pytest

from tests import db_safety
from tests.disposable_database import DisposableDatabase

ROOT = Path(__file__).resolve().parent
TEST_ENV_FILE = ROOT / ".env.test"

# Read by tests/conftest.py to prove this file ran first.
ISOLATION_ATTRIBUTE = "_medibridge_database_isolation"

_state: dict = {"database": None, "config": None, "superuser": False, "abandoned": []}


def _terminal(config):
    return config.pluginmanager.get_plugin("terminalreporter")


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """
    Validates the configuration and points the application at the session
    database. Runs before collection, so before any application module is
    imported (token_service reads the environment at import time).
    """
    try:
        file_values = db_safety.read_test_env_file(TEST_ENV_FILE)
        settings = db_safety.resolve_settings(file_values, os.environ)
        test_config = db_safety.validate_settings(settings)
    except db_safety.UnsafeTestDatabase as exc:
        raise pytest.UsageError(f"Unsafe test database configuration: {exc}") from None

    name = db_safety.new_database_name()
    db_safety.register_session_database(name)
    db_safety.apply_environment(os.environ, test_config, name)

    _state["config"] = test_config
    _state["database"] = DisposableDatabase(test_config, name)
    setattr(config, ISOLATION_ATTRIBUTE, True)


def _warn(config, message: str) -> None:
    terminal = _terminal(config)
    if terminal is not None:
        terminal.write_sep("!", message, red=True, bold=True)


def pytest_sessionstart(session):
    if _state["config"] is not None and _state["config"].allow_superuser:
        _warn(
            session.config,
            "WARNING: TEST_DB_ALLOW_SUPERUSER=1 in .env.test - tests may run as a PostgreSQL superuser",
        )


@pytest.fixture(scope="session")
def test_database_config() -> db_safety.TestDatabaseConfig:
    """The validated test configuration, for tests of the isolation machinery."""
    return _state["config"]


@pytest.fixture(scope="session", autouse=True)
def disposable_database(request):
    database: DisposableDatabase = _state["database"]
    setup_failed = False
    try:
        is_superuser, can_create_db, in_recovery = database.role_status()
        try:
            db_safety.check_role(
                is_superuser=is_superuser,
                can_create_db=can_create_db,
                in_recovery=in_recovery,
                allow_superuser=_state["config"].allow_superuser,
            )
        except db_safety.UnsafeTestDatabase as exc:
            pytest.exit(f"Unsafe test database role: {exc}", returncode=4)
        if is_superuser:
            _state["superuser"] = True
            _warn(
                request.config,
                "WARNING: connected as a PostgreSQL SUPERUSER - creating the test database anyway "
                "because .env.test allows it",
            )
        _state["abandoned"] = database.abandoned_databases()

        database.create()
        import migrate  # reads DB_NAME, which names the disposable database

        migrate.apply_migrations()
        database.open_cleanup_connection()
        database.clean()  # also proves only supported tables exist
    except BaseException:
        setup_failed = True
        raise
    finally:
        if setup_failed:
            # Setup failed part-way: drop only if identity can be proven.
            try:
                database.drop()
            except Exception:
                pass  # recorded in database.drop_error and reported in the summary

    yield database

    database.drop()


@pytest.fixture(autouse=True)
def isolated_database(disposable_database):
    """Every test starts and ends with empty application tables."""
    disposable_database.clean()
    yield
    disposable_database.clean()


def pytest_terminal_summary(terminalreporter):
    database: DisposableDatabase | None = _state["database"]
    if database is None:
        return  # configuration was rejected before a name was chosen
    terminalreporter.write_sep("-", "disposable test database")
    if not database.create_attempted:
        status = "not created"
    elif database.dropped:
        status = "dropped"
    else:
        status = "NOT DROPPED - inspect it, and drop it manually only if it is a disposable test database"
    terminalreporter.write_line(f"{database.name}: {status}")
    if database.drop_error:
        terminalreporter.write_line(f"drop refused or failed: {database.drop_error}", red=True)
    if _state["superuser"]:
        terminalreporter.write_line(
            "WARNING: tests ran as a PostgreSQL superuser (TEST_DB_ALLOW_SUPERUSER=1 in .env.test). "
            "Use a dedicated LOGIN CREATEDB role instead.",
            red=True,
        )
    if _state["abandoned"]:
        terminalreporter.write_line(
            "WARNING: disposable databases from earlier interrupted runs still exist "
            "(not deleted automatically): " + ", ".join(_state["abandoned"]),
            yellow=True,
        )
