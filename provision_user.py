"""
Create one MediBridge user account from the command line.

    python provision_user.py --database medibridge_dev --username jordan.admin \\
        --email jordan.admin@example.org --role admin --confirm-admin

The password is read twice from an interactive terminal without echo; it is never a
command-line argument. The account is stored with the existing
user_service.create_user (Argon2id hashing). Existing users are never changed.

Order of operations: arguments and identity are validated, the configured database
name is compared with --database, an interactive terminal is required, the password is
read, and only then is one database connection opened. That connection's actual
database and role are verified, and the user is inserted and committed in one
transaction on the same connection. The command does not modify os.environ.

This is an operator tool for local and demonstration environments. It is not an
identity-management system and does not change how the application authenticates users.
"""

import argparse
import contextlib
import getpass
import os
import sys
import unicodedata
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg import errors

from permissions import PERMISSIONS

# Exit codes (documented in docs/getting-started.md).
EXIT_CREATED = 0
EXIT_UNEXPECTED = 1
EXIT_INVALID_ARGUMENTS = 2
EXIT_CONFIGURATION = 3
EXIT_PASSWORD_REJECTED = 4
EXIT_DATABASE_UNAVAILABLE = 5
EXIT_ALREADY_EXISTS = 6
EXIT_OUTCOME_UNKNOWN = 7
EXIT_INTERRUPTED = 130

ROLES = tuple(sorted({role for roles in PERMISSIONS.values() for role in roles}))
REQUIRED_SETTINGS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
DEFAULT_ENV_FILE = Path(__file__).resolve().with_name(".env")
DOTENV_DISABLED_VALUES = {"1", "true", "t", "yes", "y"}  # python-dotenv's own rule
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024
MAX_USERNAME_LENGTH = 50
MAX_EMAIL_LENGTH = 255
MAX_EMAIL_LOCAL_LENGTH = 64
MAX_DOMAIN_LABEL_LENGTH = 63

# Bounds for this command's own session only; nothing else is reconfigured.
CONNECT_TIMEOUT_SECONDS = 10  # unreachable server: fail instead of hanging
LOCK_TIMEOUT = "10s"  # e.g. waiting on a concurrent insert of the same username
STATEMENT_TIMEOUT = "30s"  # any single statement of this command
APPLICATION_NAME = "medibridge-provision-user"

DUPLICATES = {
    "users_username_key": "a user with this username already exists",
    "users_email_key": "a user with this email address already exists",
}
NOT_CREATED = "no user was created (the transaction was not committed)"


class ProvisioningError(Exception):
    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class Target:
    """The explicit connection settings. The password is never part of repr()."""

    host: str
    port: str
    dbname: str
    user: str
    password: str = field(repr=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python provision_user.py",
        description=(
            "Create one MediBridge user in the database configured by DB_* settings "
            "(environment, then .env next to this script). The password is prompted for "
            "twice without echo."
        ),
        epilog=(
            "Exit codes: 0 created, 1 unexpected error, 2 invalid arguments, "
            "3 configuration or target mismatch, 4 password not accepted, "
            "5 database unavailable, busy or not migrated, 6 user already exists, "
            "7 outcome unknown (verify before retrying), 130 interrupted."
        ),
    )
    parser.add_argument(
        "--database",
        required=True,
        help="name of the target database; must equal DB_NAME and the connected database",
    )
    parser.add_argument("--username", required=True, help=f"1-{MAX_USERNAME_LENGTH} characters")
    parser.add_argument("--email", required=True, help="email address of the user")
    parser.add_argument("--role", required=True, choices=ROLES, help="role of the new user")
    parser.add_argument(
        "--confirm-admin",
        action="store_true",
        help="required together with --role admin",
    )
    return parser


# --- validation ------------------------------------------------------------------------


def has_unsafe_characters(value: str) -> bool:
    """Whitespace, control, format, surrogate, private-use or unassigned characters."""
    return any(
        c.isspace() or unicodedata.category(c) in {"Cc", "Cf", "Cs", "Co", "Cn"} for c in value
    )


def safe_text(value) -> str:
    """For terminal output: unsafe characters are shown escaped, never interpreted."""
    text = str(value)
    if not has_unsafe_characters(text.replace(" ", "")):
        return text
    return text.encode("unicode_escape").decode("ascii")


def valid_email(email: str) -> bool:
    """
    CLI-level sanity check, not an RFC 5322 parser or an application-wide policy:
    exactly one @, a non-empty local part of at most 64 characters without leading,
    trailing or consecutive dots, and a domain of at least two dot-separated labels of
    ASCII letters, digits and inner hyphens (internationalised domains in punycode),
    whose last label contains a letter.
    """
    if len(email) > MAX_EMAIL_LENGTH or has_unsafe_characters(email) or email.count("@") != 1:
        return False
    local, domain = email.split("@")
    if not 0 < len(local) <= MAX_EMAIL_LOCAL_LENGTH:
        return False
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    labels = domain.split(".")
    if len(labels) < 2:
        return False
    for label in labels:
        if not 0 < len(label) <= MAX_DOMAIN_LABEL_LENGTH:
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
        if not all(c.isascii() and (c.isalnum() or c == "-") for c in label):
            return False
    return any(c.isalpha() for c in labels[-1])


def validate_identity(username: str, email: str) -> None:
    if not username or has_unsafe_characters(username):
        raise ProvisioningError(
            "username must not be blank or contain whitespace or control characters",
            EXIT_INVALID_ARGUMENTS,
        )
    if len(username) > MAX_USERNAME_LENGTH:
        raise ProvisioningError(
            f"username must be at most {MAX_USERNAME_LENGTH} characters", EXIT_INVALID_ARGUMENTS
        )
    if not valid_email(email):
        raise ProvisioningError("email address is not valid", EXIT_INVALID_ARGUMENTS)


# --- configuration ---------------------------------------------------------------------


def load_settings(environ=None, env_file: Path | None = None) -> dict:
    """
    The settings this command connects with. Injected settings are used as given.
    Otherwise: the process environment, then .env next to this script for names the
    environment does not set (the same precedence as the application's load_dotenv()).
    PYTHON_DOTENV_DISABLED is honoured. os.environ is not modified.
    """
    if environ is not None:
        return dict(environ)
    env_file = DEFAULT_ENV_FILE if env_file is None else env_file
    values = {}
    disabled = os.environ.get("PYTHON_DOTENV_DISABLED", "").casefold() in DOTENV_DISABLED_VALUES
    if not disabled and env_file.is_file():
        from dotenv import dotenv_values

        values = {k: v for k, v in dotenv_values(env_file).items() if v is not None}
    values.update(os.environ)
    return values


def resolve_target(database: str, settings) -> Target:
    missing = [name for name in REQUIRED_SETTINGS if not settings.get(name)]
    if missing:
        raise ProvisioningError(
            f"missing database settings: {', '.join(missing)}", EXIT_CONFIGURATION
        )
    if settings["DB_NAME"] != database:
        raise ProvisioningError(
            "--database does not match the configured DB_NAME; nothing was changed",
            EXIT_CONFIGURATION,
        )
    return Target(
        host=settings["DB_HOST"],
        port=str(settings["DB_PORT"]),
        dbname=settings["DB_NAME"],
        user=settings["DB_USER"],
        password=settings["DB_PASSWORD"],
    )


# --- terminal --------------------------------------------------------------------------


def windows_console_mode_ok(fd: int) -> bool:
    """True if the handle behind fd is a console input (GetConsoleMode succeeds); NUL,
    files and pipes fail. Uses a private WinDLL with declared argument and return types
    so the 64-bit HANDLE is not truncated to a C int."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_console_mode = kernel32.GetConsoleMode
    get_console_mode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    get_console_mode.restype = wintypes.BOOL
    mode = wintypes.DWORD()
    return bool(get_console_mode(wintypes.HANDLE(msvcrt.get_osfhandle(fd)), ctypes.byref(mode)))


def stdin_is_interactive(stream=None, *, os_name=None, console_check=None) -> bool:
    """True only for a real terminal. On Windows, NUL also reports isatty(), so the
    console mode of the stdin handle is checked as well. Any failure means False."""
    stream = sys.stdin if stream is None else stream
    os_name = os.name if os_name is None else os_name
    console_check = windows_console_mode_ok if console_check is None else console_check
    try:
        if stream is None or not stream.isatty():
            return False
        if os_name != "nt":
            return True
        return bool(console_check(stream.fileno()))
    except Exception:  # every failure to prove a console means "no console"
        return False


def read_password_securely(prompt: str) -> str:
    """Reads a password without echo, or fails; never falls back to visible input."""
    if not stdin_is_interactive():
        raise ProvisioningError(
            "an interactive terminal is required to enter the password", EXIT_PASSWORD_REJECTED
        )
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except getpass.GetPassWarning:
            raise ProvisioningError(
                "the terminal cannot hide password input", EXIT_PASSWORD_REJECTED
            ) from None


def obtain_password(read_password) -> str:
    password = read_password("Password: ")
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ProvisioningError(
            f"password must be {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters",
            EXIT_PASSWORD_REJECTED,
        )
    if read_password("Confirm password: ") != password:
        raise ProvisioningError(
            "passwords do not match; nothing was changed", EXIT_PASSWORD_REJECTED
        )
    return password


# --- database --------------------------------------------------------------------------


def open_connection(target: Target):
    """One bounded connection for this command; settings apply to this session only."""
    try:
        return psycopg.connect(
            host=target.host,
            port=target.port,
            dbname=target.dbname,
            user=target.user,
            password=target.password,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            application_name=APPLICATION_NAME,
            options=f"-c lock_timeout={LOCK_TIMEOUT} -c statement_timeout={STATEMENT_TIMEOUT}",
        )
    except psycopg.Error:
        # The driver's message is not shown: it can echo connection parameters.
        raise ProvisioningError(
            f"could not connect to {safe_text(target.host)}:{safe_text(target.port)} as "
            f"{safe_text(target.user)} within {CONNECT_TIMEOUT_SECONDS} s (server unreachable, "
            f"database missing or authentication failed); {NOT_CREATED}",
            EXIT_DATABASE_UNAVAILABLE,
        ) from None


def verify_connected_target(conn, target: Target, database: str) -> dict:
    """
    Checks the session that will perform the insert: the actual database and role, and the
    endpoint libpq reports for this connection. This catches accidental mis-targeting (for
    example a connection-pooler alias that maps the requested name to another database); it
    is not an authentication of the server, which requires TLS with certificate verification.
    """
    actual_db, current_user, session_user = conn.execute(
        "SELECT current_database(), current_user, session_user"
    ).fetchone()
    endpoint = {"host": conn.info.host, "port": str(conn.info.port)}
    problems = []
    if not (actual_db == database == target.dbname):
        problems.append(f"connected database is {safe_text(actual_db)}")
    if not (current_user == session_user == target.user):
        problems.append(f"connected role is {safe_text(current_user)}")
    if endpoint["port"] != target.port:
        problems.append(f"connected port is {safe_text(endpoint['port'])}")
    if problems:
        raise ProvisioningError(
            "the connected target does not match the requested one ("
            + "; ".join(problems)
            + f"); {NOT_CREATED}",
            EXIT_CONFIGURATION,
        )
    return {"database": actual_db, "role": current_user, **endpoint}


def failure_before_commit(exc: BaseException) -> ProvisioningError:
    """Maps a failure that happened before COMMIT was sent: PostgreSQL cannot have
    committed the transaction, whatever the state of the connection."""
    if isinstance(exc, ProvisioningError):
        return exc
    if isinstance(exc, KeyboardInterrupt):
        return ProvisioningError(f"interrupted; {NOT_CREATED}", EXIT_INTERRUPTED)
    if isinstance(exc, errors.UniqueViolation) and exc.diag.constraint_name in DUPLICATES:
        return ProvisioningError(
            f"{DUPLICATES[exc.diag.constraint_name]}; {NOT_CREATED}", EXIT_ALREADY_EXISTS
        )
    if isinstance(exc, errors.UndefinedTable):
        return ProvisioningError(
            f"the users table does not exist; run `python migrate.py` first; {NOT_CREATED}",
            EXIT_DATABASE_UNAVAILABLE,
        )
    if isinstance(exc, errors.LockNotAvailable | errors.QueryCanceled):
        return ProvisioningError(
            f"the database did not respond within this command's timeouts; {NOT_CREATED}",
            EXIT_DATABASE_UNAVAILABLE,
        )
    if isinstance(exc, psycopg.OperationalError):
        return ProvisioningError(
            f"the database connection failed; {NOT_CREATED}", EXIT_DATABASE_UNAVAILABLE
        )
    if isinstance(exc, psycopg.Error):
        return ProvisioningError(
            f"unexpected database error (SQLSTATE {exc.sqlstate}); {NOT_CREATED}",
            EXIT_UNEXPECTED,
        )
    return ProvisioningError(
        f"unexpected error ({type(exc).__name__}); {NOT_CREATED}", EXIT_UNEXPECTED
    )


def commit_or_report(conn, username: str) -> None:
    try:
        conn.commit()
    except BaseException as exc:
        if isinstance(exc, psycopg.Error) and exc.sqlstate and not conn.closed:
            # The server answered COMMIT with an error: the transaction was rolled back.
            raise ProvisioningError(
                f"the database rejected the commit (SQLSTATE {exc.sqlstate}); {NOT_CREATED}",
                EXIT_UNEXPECTED,
            ) from None
        # COMMIT may have reached the server; its outcome was not received. Never retry.
        raise ProvisioningError(
            "the outcome of the commit is unknown: the user may or may not have been "
            f"created. Do not run the command again until you have checked whether user "
            f"{safe_text(username)} exists (docs/getting-started.md, step 6).",
            EXIT_OUTCOME_UNKNOWN,
        ) from None


def persist(target: Target, database: str, username: str, email: str, password: str, role):
    """Connects, verifies the target on that connection and inserts the user in one
    transaction that this function owns; commits only after every check passed."""
    from user_service import create_user  # shared Argon2id hashing and INSERT

    conn = open_connection(target)
    try:
        try:
            identity = verify_connected_target(conn, target, database)
            user = create_user(
                username=username, email=email, password=password, role=role, conn=conn
            )
        except BaseException as exc:
            # Best effort: COMMIT was never sent, so nothing can have been committed.
            with contextlib.suppress(Exception):
                conn.rollback()
            raise failure_before_commit(exc) from None
        commit_or_report(conn, username)
        return user, identity
    finally:
        conn.close()


# --- command ---------------------------------------------------------------------------


def main(argv=None, *, read_password=read_password_securely, environ=None, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        if args.role == "admin" and not args.confirm_admin:
            raise ProvisioningError(
                "creating an admin requires --confirm-admin", EXIT_INVALID_ARGUMENTS
            )
        validate_identity(args.username, args.email)
        target = resolve_target(args.database, load_settings(environ))
        out.write(
            f"Target: database {safe_text(target.dbname)} on {safe_text(target.host)}:"
            f"{safe_text(target.port)} as {safe_text(target.user)}. "
            f"Creating {args.role} user {args.username}.\n"
        )
        try:
            password = obtain_password(read_password)
        except KeyboardInterrupt, EOFError:
            # Nothing has connected to the database yet.
            raise ProvisioningError(
                "interrupted during password entry; nothing was changed", EXIT_INTERRUPTED
            ) from None
        user, identity = persist(
            target, args.database, args.username, args.email, password, args.role
        )
    except ProvisioningError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return exc.exit_code
    out.write(
        f"Created {user['role']} user {user['username']} (id {user['id']}) in database "
        f"{safe_text(identity['database'])} as {safe_text(identity['role'])} on "
        f"{safe_text(identity['host'])}:{safe_text(identity['port'])}; committed.\n"
    )
    return EXIT_CREATED


if __name__ == "__main__":
    sys.exit(main())
