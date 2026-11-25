"""
Safety checks for the disposable PostgreSQL database used by the test suite.

The suite never runs against an existing database. Each pytest session
creates its own database, and every destructive step (truncate, drop) first
proves that the target is that database:

1. Explicit configuration: `MEDIBRIDGE_ENV=test`, all connection settings
   present, the database host on an allow list, and no inherited libpq
   variable that could redirect a connection or change its session
   (`PGHOSTADDR`, `PGSERVICE`, `PGOPTIONS`). The harness's own connections
   (create, clean, drop) also check where they actually arrived.
2. Name: `medibridge_test_` followed by 16 random hex characters, equal to the
   name this process generated.
3. Identity: the PostgreSQL OID and owner recorded right after this process
   created the database.
4. Ownership marker: a database comment written after creation.

Error messages name missing or invalid settings but never print their values,
except the database host and the address a connection reached.
"""

import ipaddress
import re
import secrets
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

DATABASE_PREFIX = "medibridge_test_"
DATABASE_NAME_PATTERN = re.compile(r"^medibridge_test_[0-9a-f]{16}$")
OWNERSHIP_MARKER = "medibridge-disposable-test-database"
SUPPORTED_SCHEMA = "public"
PRESERVED_TABLES = frozenset({"schema_migrations"})

# Settings the application reads. When a test env file exists they come only
# from that file: keys it omits are removed from the environment, so neither
# the development `.env` nor the shell can supply them.
CONNECTION_KEYS = (
    "DB_HOST",
    "DB_PORT",
    "DB_USER",
    "DB_PASSWORD",
    "JWT_SECRET_KEY",
    "JWT_ALGORITHM",
    "JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
    # Optional limits of the shared SQLAlchemy engine (db_engine.py).
    "DB_POOL_SIZE",
    "DB_POOL_MAX_OVERFLOW",
    "DB_POOL_TIMEOUT_SECONDS",
    "DB_CONNECT_TIMEOUT_SECONDS",
    "DB_STATEMENT_TIMEOUT_SECONDS",
    "DB_LOCK_TIMEOUT_SECONDS",
)
REQUIRED_KEYS = ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "JWT_SECRET_KEY")

# Test-harness switches that may come from the test env file or the shell.
FLAG_KEYS = (
    "MEDIBRIDGE_ENV",
    "TEST_DB_ALLOWED_HOSTS",
    "TEST_DB_MAINTENANCE_DB",
)
# Accepted only from the local test env file, never from the environment, so
# CI (which has no such file) can never run as a superuser.
SUPERUSER_OVERRIDE_KEY = "TEST_DB_ALLOW_SUPERUSER"

# libpq reads these from the environment even though every test connection passes
# host, port, user, password and dbname explicitly. Inherited from the shell they
# are refused before the first connection. Tests that need one (the concurrency and
# engine tests set PGOPTIONS) set it themselves, after this check.
UNCONTROLLED_LIBPQ_VARIABLES = {
    "PGHOSTADDR": "sends connections to another address while the host still reads as DB_HOST",
    "PGSERVICE": "loads connection settings, such as hostaddr or options, from a service file",
    "PGOPTIONS": "sets server session parameters, such as search_path or role, on every connection",
}

DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "::1")
DEFAULT_MAINTENANCE_DB = "postgres"


class UnsafeTestDatabase(RuntimeError):
    """Raised when the test database cannot be proven safe to use."""


@dataclass(frozen=True)
class TestDatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    maintenance_db: str
    allow_superuser: bool
    # Application settings to export into os.environ for the test session.
    app_settings: dict[str, str]

    def connect_kwargs(self, dbname: str) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "dbname": dbname,
        }


# --- configuration -------------------------------------------------------------


def read_test_env_file(path: Path) -> dict[str, str] | None:
    """
    Reads the test env file, or returns None when it does not exist.

    Interpolation is off, so a value such as `${DB_PASSWORD}` stays literal
    instead of being filled in from the shell environment.
    """
    if not path.is_file():
        return None
    values = dotenv_values(path, interpolate=False)
    return {key: value for key, value in values.items() if value is not None}


def resolve_settings(
    file_values: Mapping[str, str] | None,
    process_env: Mapping[str, str],
) -> dict[str, str]:
    """
    Combines the test env file and the process environment.

    `file_values` is None when there is no test env file (as in CI); then the
    process environment supplies the connection settings. When the file
    exists, connection settings come only from it.
    """
    check_libpq_environment(process_env)
    if process_env.get(SUPERUSER_OVERRIDE_KEY):
        raise UnsafeTestDatabase(
            f"{SUPERUSER_OVERRIDE_KEY} is only accepted from the local .env.test file, "
            "not from the environment. Unset it."
        )

    connection_source = file_values if file_values is not None else process_env
    settings = {
        key: str(connection_source[key]) for key in CONNECTION_KEYS if connection_source.get(key)
    }
    for key in FLAG_KEYS:
        value = (file_values or {}).get(key) or process_env.get(key)
        if value:
            settings[key] = str(value)
    if file_values and file_values.get(SUPERUSER_OVERRIDE_KEY):
        settings[SUPERUSER_OVERRIDE_KEY] = str(file_values[SUPERUSER_OVERRIDE_KEY])
    return settings


def check_libpq_environment(process_env: Mapping[str, str]) -> None:
    """
    Refuses inherited libpq variables that could redirect a test connection or change
    its session, whatever their value (even empty). Names them, never their values.

    Others are left alone: PGHOST, PGPORT, PGDATABASE, PGUSER and PGPASSWORD are
    overridden by the explicit settings; PGPASSFILE is not consulted when a password
    is given; PGSERVICEFILE and PGSYSCONFDIR only matter together with PGSERVICE;
    transport (PGSSLMODE and similar), timing (PGCONNECT_TIMEOUT) and presentation
    settings (PGTZ, PGDATESTYLE, PGCLIENTENCODING, PGAPPNAME) do not change where a
    connection goes or what the safety checks see.
    """
    present = [name for name in UNCONTROLLED_LIBPQ_VARIABLES if name in process_env]
    if present:
        reasons = "; ".join(f"{name} {UNCONTROLLED_LIBPQ_VARIABLES[name]}" for name in present)
        raise UnsafeTestDatabase(
            f"Unset {', '.join(present)} before running the tests ({reasons}). For example "
            f"`unset {present[0]}` in bash or `Remove-Item Env:{present[0]}` in PowerShell."
        )


def _is_unix_socket_path(host: str) -> bool:
    # libpq's rule: an absolute path (on Windows also with a backslash or a drive
    # letter), or "@" for an abstract socket on Linux.
    if host.startswith(("/", "\\", "@")):
        return True
    return len(host) > 2 and host[0].isalpha() and host[1] == ":" and host[2] in "/\\"


def check_destination(info, host: str) -> None:
    """
    Checks the address a connection actually reached (psycopg's `conn.info.hostaddr`,
    "" for a Unix socket), before any statement runs on it. It is the only evidence of
    the real destination: `conn.info.host` and `conn.info.port` repeat the settings that
    were passed in, even when PGHOSTADDR or a service file sent the connection elsewhere.

    - Unix-socket path: the connection must be a socket connection.
    - IP address: the connection must have reached exactly that address.
    - `localhost`: the connection must have reached a loopback address.
    - Any other host name (only if added to TEST_DB_ALLOWED_HOSTS): only a TCP connection
      is required. The name is not resolved here, so where it leads is not verified.

    The check runs after the connection is established, so it keeps statements away
    from a wrong server but cannot keep the password from reaching it; preventing the
    redirection is the job of check_libpq_environment.
    """
    address = info.hostaddr or ""
    refused = UnsafeTestDatabase(
        f"The connection reached {address or 'a Unix socket'}, not DB_HOST '{host}'."
    )
    if _is_unix_socket_path(host):
        if address:
            raise refused
        return
    try:
        reached = ipaddress.ip_address(address)
    except ValueError:
        raise refused from None
    try:
        configured = ipaddress.ip_address(host)
    except ValueError:
        configured = None
    if configured is not None and reached != configured:
        raise refused
    if configured is None and host.lower() == "localhost" and not reached.is_loopback:
        raise refused


def validate_settings(settings: Mapping[str, str]) -> TestDatabaseConfig:
    if settings.get("MEDIBRIDGE_ENV") != "test":
        raise UnsafeTestDatabase(
            "MEDIBRIDGE_ENV must be set to 'test' to run the test suite. "
            "Add it to .env.test or the CI environment."
        )

    missing = [key for key in REQUIRED_KEYS if not settings.get(key)]
    if missing:
        raise UnsafeTestDatabase(
            "Missing required test settings: " + ", ".join(missing) + ". "
            "Development settings are never used as a fallback."
        )

    allowed_hosts = tuple(
        host.strip().lower()
        for host in settings.get("TEST_DB_ALLOWED_HOSTS", ",".join(DEFAULT_ALLOWED_HOSTS)).split(
            ","
        )
        if host.strip()
    )
    host = settings["DB_HOST"].strip()
    if host.lower() not in allowed_hosts:
        raise UnsafeTestDatabase(
            f"DB_HOST '{host}' is not an allowed test database host "
            f"({', '.join(allowed_hosts)}). Set TEST_DB_ALLOWED_HOSTS to allow it."
        )

    try:
        port = int(settings["DB_PORT"])
    except ValueError:
        raise UnsafeTestDatabase("DB_PORT must be a number.") from None

    maintenance_db = settings.get("TEST_DB_MAINTENANCE_DB", DEFAULT_MAINTENANCE_DB)
    if is_disposable_name(maintenance_db):
        raise UnsafeTestDatabase("TEST_DB_MAINTENANCE_DB must not be a disposable test database.")

    app_settings = {key: settings[key] for key in CONNECTION_KEYS if key in settings}

    return TestDatabaseConfig(
        host=host,
        port=port,
        user=settings["DB_USER"],
        password=settings["DB_PASSWORD"],
        maintenance_db=maintenance_db,
        allow_superuser=settings.get(SUPERUSER_OVERRIDE_KEY) == "1",
        app_settings=app_settings,
    )


def apply_environment(
    environ: MutableMapping[str, str],
    config: TestDatabaseConfig,
    session_database: str,
) -> None:
    """
    Makes the process environment exactly what the application should see.

    Connection settings the test configuration does not define are removed,
    so the application falls back to its own coded defaults (for example a
    30-minute token lifetime) rather than to a value inherited from the shell.
    """
    for key in CONNECTION_KEYS:
        if key in config.app_settings:
            environ[key] = config.app_settings[key]
        else:
            environ.pop(key, None)
    environ["MEDIBRIDGE_ENV"] = "test"
    # The application's own load_dotenv() calls become no-ops, so the
    # development `.env` file is never read during tests.
    environ["PYTHON_DOTENV_DISABLED"] = "1"
    # Points the application at the session database before it exists, so a
    # premature connection fails instead of reaching some other database.
    environ["DB_NAME"] = session_database


# --- names and session identity ----------------------------------------------------

_session = {"database": None}


def new_database_name() -> str:
    """A database name with 64 bits of cryptographic randomness."""
    return DATABASE_PREFIX + secrets.token_hex(8)


def is_disposable_name(name: str) -> bool:
    return bool(DATABASE_NAME_PATTERN.fullmatch(name))


def register_session_database(name: str) -> None:
    if not is_disposable_name(name):
        raise UnsafeTestDatabase(
            "The session database name is not a disposable test database name."
        )
    _session["database"] = name


def is_session_database(name: str) -> bool:
    """True only for the exact database created for this pytest session."""
    registered = _session["database"]
    return registered is not None and name == registered and is_disposable_name(name)


# --- role and server ----------------------------------------------------------------


def check_role(
    *, is_superuser: bool, can_create_db: bool, in_recovery: bool, allow_superuser: bool
) -> None:
    """Checks the connected role and server before anything is created."""
    if in_recovery:
        raise UnsafeTestDatabase("The test database server is a read-only replica.")
    if is_superuser and not allow_superuser:
        raise UnsafeTestDatabase(
            "The test role is a PostgreSQL superuser. Use a dedicated role with only "
            "LOGIN and CREATEDB (see .env.test.example), or set TEST_DB_ALLOW_SUPERUSER=1 "
            "in the local .env.test file to accept the risk explicitly."
        )
    if not (is_superuser or can_create_db):
        raise UnsafeTestDatabase("The test role needs the CREATEDB privilege.")


# --- identity before truncate or drop ----------------------------------------------


@dataclass(frozen=True)
class CreatedDatabase:
    """What this process recorded immediately after its own CREATE DATABASE."""

    name: str
    oid: int
    owner_oid: int


@dataclass(frozen=True)
class FoundDatabase:
    """The database currently on the server under the session name."""

    oid: int
    owner_oid: int
    comment: str | None


IDENTITY_QUERY = """
    SELECT d.oid, d.datdba, shobj_description(d.oid, 'pg_database'),
           (SELECT oid FROM pg_roles WHERE rolname = current_user)
    FROM pg_database AS d
    WHERE d.datname = %s
"""

CURRENT_ROLE_QUERY = "SELECT oid FROM pg_roles WHERE rolname = current_user"


def check_identity(
    *,
    session_name: str,
    created: CreatedDatabase | None,
    found: FoundDatabase | None,
    current_role_oid: int,
    require_marker: bool,
) -> None:
    """
    Raises unless `found` is exactly the database this process created.

    `require_marker` is False only on the exceptional path where this process
    never managed to write the ownership marker. Even then, the recorded OID
    and owner must match and any comment that is present must be the marker.
    """
    if not is_disposable_name(session_name):
        raise UnsafeTestDatabase(
            "Refusing to touch a database that is not a disposable test database."
        )
    if created is None:
        raise UnsafeTestDatabase(
            "This process could not record the identity of the database it created, "
            "so ownership cannot be established."
        )
    if created.name != session_name:
        raise UnsafeTestDatabase(
            "The recorded database is not this session's disposable test database."
        )
    if found is None:
        raise UnsafeTestDatabase("This session's disposable test database no longer exists.")
    if found.oid != created.oid:
        raise UnsafeTestDatabase("A different database now has this session's name (OID changed).")
    if found.owner_oid != created.owner_oid or found.owner_oid != current_role_oid:
        raise UnsafeTestDatabase("The disposable test database is not owned by the test role.")
    if require_marker and found.comment != OWNERSHIP_MARKER:
        raise UnsafeTestDatabase("The disposable test database is missing its ownership marker.")
    if not require_marker and found.comment not in (None, OWNERSHIP_MARKER):
        raise UnsafeTestDatabase("The disposable test database has an unexpected comment.")


def fetch_identity(conn, name: str) -> tuple[FoundDatabase | None, int]:
    """The database currently named `name`, and the OID of the connected role."""
    row = conn.execute(IDENTITY_QUERY, (name,)).fetchone()
    if row is None:
        return None, conn.execute(CURRENT_ROLE_QUERY).fetchone()[0]
    oid, owner_oid, comment, current_role_oid = row
    return FoundDatabase(oid=oid, owner_oid=owner_oid, comment=comment), current_role_oid


# --- cleanup scope ---------------------------------------------------------------------

TABLES_QUERY = """
    SELECT schemaname, tablename
    FROM pg_tables
    WHERE schemaname <> 'information_schema'
      AND schemaname NOT LIKE 'pg\\_%'
    ORDER BY schemaname, tablename
"""  # executed without parameters, so '%' is not a placeholder


def cleanup_targets(tables: Iterable[tuple[str, str]]) -> list[str]:
    """
    The tables to empty between tests.

    Fails instead of silently skipping data: every user table must be in the
    supported schema, and there must be at least one application table.
    """
    tables = list(tables)
    unsupported = sorted(
        f"{schema}.{table}" for schema, table in tables if schema != SUPPORTED_SCHEMA
    )
    if unsupported:
        raise UnsafeTestDatabase(
            "Tables outside the supported schema would not be cleaned between tests: "
            + ", ".join(unsupported)
        )
    targets = sorted(table for _, table in tables if table not in PRESERVED_TABLES)
    if not targets:
        raise UnsafeTestDatabase("No application tables were found to clean in the test database.")
    return targets


# --- leftovers from earlier runs ----------------------------------------------------------


def find_abandoned_databases(conn, current_name: str) -> list[str]:
    """Disposable test databases left by earlier runs. Reported, never dropped."""
    rows = conn.execute(
        """
        SELECT datname
        FROM pg_database
        WHERE datname LIKE 'medibridge\\_test\\_%%'
          AND datname <> %s
        ORDER BY datname
        """,
        (current_name,),
    ).fetchall()
    return [name for (name,) in rows if is_disposable_name(name)]
