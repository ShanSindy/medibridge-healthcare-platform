"""
Database isolation for the MediBridge test suite.

Every pytest session gets its own disposable PostgreSQL database:

- Created on the configured test server with a cryptographically random name
  (`medibridge_test_<16 hex>`), marked with an ownership comment, and built
  with the project's own migrations (`migrate.py`).
- Emptied before and after every test. All application tables are truncated
  in one statement with CASCADE; `schema_migrations` is kept.
- Dropped at the end of the session.

No existing database (development, `medibridge_test`, `medibridge_ci_test`)
is read, modified or dropped. Every truncate and the final drop first prove
the target is this session's database (see tests/db_safety.py).

Configuration comes from `.env.test` (see `.env.test.example`) or, when that
file is absent as in CI, from the process environment. The application's own
`load_dotenv()` calls are disabled for the run, so the development `.env` can
never supply a value.

Limitation: if the process is killed (for example with SIGKILL), its
database is left behind. Later runs report such databases but never delete
them.
"""

import os
from pathlib import Path

import psycopg
import pytest
from dotenv import dotenv_values
from psycopg import sql

from tests import db_safety

ROOT = Path(__file__).resolve().parent
TEST_ENV_FILE = ROOT / ".env.test"


CONFIG: db_safety.TestDatabaseConfig
SESSION_DATABASE: str
_report: dict = {"abandoned": [], "dropped": False}


def pytest_configure(config):
    """
    Validates the test configuration and points the application at the
    session database. Runs before test collection, so before any application
    module is imported (token_service, for example, reads the environment at
    import time).
    """
    global CONFIG, SESSION_DATABASE

    file_values = dotenv_values(TEST_ENV_FILE) if TEST_ENV_FILE.is_file() else None
    settings = db_safety.resolve_settings(file_values, os.environ)
    try:
        CONFIG = db_safety.validate_settings(settings)
    except db_safety.UnsafeTestDatabase as exc:
        raise pytest.UsageError(f"Unsafe test database configuration: {exc}") from None

    os.environ.update(CONFIG.app_settings)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    SESSION_DATABASE = db_safety.new_database_name()
    # Point the application at the disposable database before it exists, so a
    # premature connection fails instead of reaching some other database.
    os.environ["DB_NAME"] = SESSION_DATABASE


def _admin_connection() -> psycopg.Connection:
    return psycopg.connect(**CONFIG.connect_kwargs(CONFIG.maintenance_db), autocommit=True)


class DisposableDatabase:
    """The session database and the connection used to clean it."""

    def __init__(self, name: str, connection: psycopg.Connection, tables: list[str]):
        self.name = name
        self.connection = connection
        self.tables = tables
        table_list = sql.SQL(", ").join(sql.Identifier(table) for table in tables)
        self._truncate = sql.SQL("TRUNCATE {} CASCADE").format(table_list)
        self._has_rows = sql.SQL("SELECT {}").format(
            sql.SQL(" OR ").join(
                sql.SQL("EXISTS (SELECT 1 FROM {})").format(sql.Identifier(table)) for table in tables
            )
        )

    def clean(self) -> None:
        """
        Empties every application table. Failures are raised, never ignored.

        TRUNCATE is skipped when every table is already empty, which is the
        case for most tests. Ownership is verified either way.
        """
        db_safety.verify_ownership(self.connection, self.name, connected_to_it=True)
        if self.connection.execute(self._has_rows).fetchone()[0]:
            self.connection.execute(self._truncate)

    def is_empty(self) -> bool:
        return not self.connection.execute(self._has_rows).fetchone()[0]


def _create_database() -> None:
    with _admin_connection() as admin:
        with admin.cursor() as cur:
            cur.execute(
                "SELECT rolsuper, rolcreatedb, pg_is_in_recovery() FROM pg_roles WHERE rolname = current_user"
            )
            is_superuser, can_create_db, in_recovery = cur.fetchone()
        try:
            db_safety.check_role(
                is_superuser=is_superuser,
                can_create_db=can_create_db,
                in_recovery=in_recovery,
                allow_superuser=CONFIG.allow_superuser,
            )
        except db_safety.UnsafeTestDatabase as exc:
            pytest.exit(f"Unsafe test database role: {exc}", returncode=4)
        _report["superuser"] = is_superuser
        _report["abandoned"] = db_safety.find_abandoned_databases(admin, SESSION_DATABASE)

        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(SESSION_DATABASE)))
        _report["created"] = True
        admin.execute(
            sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(SESSION_DATABASE), sql.Literal(db_safety.OWNERSHIP_MARKER)
            )
        )


def _drop_database() -> None:
    if not _report.get("created"):
        return
    with _admin_connection() as admin:
        db_safety.verify_ownership(admin, SESSION_DATABASE, connected_to_it=False)
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(SESSION_DATABASE)))
    _report["dropped"] = True


@pytest.fixture(scope="session", autouse=True)
def disposable_database():
    _create_database()
    cleanup_connection = None
    try:
        import migrate  # reads DB_NAME, which now names the disposable database

        migrate.apply_migrations()

        cleanup_connection = psycopg.connect(
            **CONFIG.connect_kwargs(SESSION_DATABASE), autocommit=True
        )
        # Fail fast instead of hanging if a test leaves a transaction open.
        cleanup_connection.execute("SET lock_timeout = '5s'")
        db_safety.verify_ownership(cleanup_connection, SESSION_DATABASE, connected_to_it=True)
        with cleanup_connection.cursor() as cur:
            cur.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public' AND tablename <> 'schema_migrations'
                ORDER BY tablename
                """
            )
            tables = [name for (name,) in cur.fetchall()]

        yield DisposableDatabase(SESSION_DATABASE, cleanup_connection, tables)
    finally:
        if cleanup_connection is not None:
            cleanup_connection.close()
        _drop_database()


@pytest.fixture(autouse=True)
def isolated_database(disposable_database):
    """Every test starts and ends with empty application tables."""
    disposable_database.clean()
    yield
    disposable_database.clean()


def pytest_terminal_summary(terminalreporter):
    if "SESSION_DATABASE" not in globals():
        return  # configuration was rejected before a name was chosen
    terminalreporter.write_sep("-", "disposable test database")
    if not _report.get("created"):
        status = "not created"
    elif _report["dropped"]:
        status = "dropped"
    else:
        status = "NOT DROPPED - remove it manually after checking it is a disposable test database"
    terminalreporter.write_line(f"{SESSION_DATABASE}: {status}")
    if _report.get("superuser"):
        terminalreporter.write_line(
            "WARNING: tests ran as a PostgreSQL superuser (TEST_DB_ALLOW_SUPERUSER=1). "
            "Use a dedicated LOGIN CREATEDB role instead."
        )
    if _report["abandoned"]:
        terminalreporter.write_line(
            "WARNING: disposable databases from earlier interrupted runs still exist "
            "(not deleted automatically): " + ", ".join(_report["abandoned"])
        )
