"""
Create one MediBridge user account from the command line.

    python provision_user.py --database medibridge_dev --username jordan.admin \\
        --email jordan.admin@example.org --role admin --confirm-admin

The password is read twice from the terminal without echo; it is never a
command-line argument. The account is stored with the existing
user_service.create_user (Argon2id hashing). Existing users are never changed.

This is an operator tool for local and demonstration environments. It is not an
identity-management system and does not change how the application
authenticates users.
"""

import argparse
import getpass
import os
import sys
import warnings

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
EXIT_INTERRUPTED = 130

ROLES = tuple(sorted({role for roles in PERMISSIONS.values() for role in roles}))
REQUIRED_SETTINGS = ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024
MAX_USERNAME_LENGTH = 50
MAX_EMAIL_LENGTH = 255
DUPLICATES = {
    "users_username_key": "a user with this username already exists",
    "users_email_key": "a user with this email address already exists",
}


class ProvisioningError(Exception):
    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python provision_user.py",
        description=(
            "Create one MediBridge user in the database configured by DB_* settings "
            "(environment or .env). The password is prompted for twice without echo."
        ),
        epilog=(
            "Exit codes: 0 created, 1 unexpected error, 2 invalid arguments, "
            "3 configuration or target mismatch, 4 password not accepted, "
            "5 database unavailable or not migrated, 6 user already exists, 130 interrupted."
        ),
    )
    parser.add_argument(
        "--database",
        required=True,
        help="name of the target database; must equal the configured DB_NAME",
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


def validate_identity(username: str, email: str) -> None:
    if not username or username != username.strip() or any(c.isspace() for c in username):
        raise ProvisioningError(
            "username must not be blank or contain whitespace", EXIT_INVALID_ARGUMENTS
        )
    if len(username) > MAX_USERNAME_LENGTH:
        raise ProvisioningError(
            f"username must be at most {MAX_USERNAME_LENGTH} characters", EXIT_INVALID_ARGUMENTS
        )
    local, _, domain = email.partition("@")
    if (
        email != email.strip()
        or len(email) > MAX_EMAIL_LENGTH
        or not local
        or "." not in domain
        or any(c.isspace() for c in email)
    ):
        raise ProvisioningError("email address is not valid", EXIT_INVALID_ARGUMENTS)


def check_target(database: str, environ) -> None:
    missing = [name for name in REQUIRED_SETTINGS if not environ.get(name)]
    if missing:
        raise ProvisioningError(
            f"missing database settings: {', '.join(missing)}", EXIT_CONFIGURATION
        )
    if environ["DB_NAME"] != database:
        raise ProvisioningError(
            "--database does not match the configured DB_NAME; nothing was changed",
            EXIT_CONFIGURATION,
        )


def stdin_is_interactive() -> bool:
    """True only for a real terminal. On Windows, NUL also reports isatty(), so the console
    mode of the stdin handle is checked as well."""
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return False
        if os.name != "nt":
            return True
        import ctypes
        import msvcrt

        mode = ctypes.c_uint32()
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
    except OSError, ValueError, AttributeError:
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


def persist(username: str, email: str, password: str, role: str) -> dict:
    from user_service import create_user  # imported late: reads DB_* settings via .env

    try:
        return create_user(username=username, email=email, password=password, role=role)
    except errors.UniqueViolation as exc:
        message = DUPLICATES.get(exc.diag.constraint_name)
        if message is None:
            raise
        raise ProvisioningError(f"{message}; nothing was changed", EXIT_ALREADY_EXISTS) from None
    except errors.UndefinedTable:
        raise ProvisioningError(
            "the users table does not exist; run `python migrate.py` first",
            EXIT_DATABASE_UNAVAILABLE,
        ) from None
    except psycopg.OperationalError:
        raise ProvisioningError(
            "could not connect to the configured database", EXIT_DATABASE_UNAVAILABLE
        ) from None


def main(argv=None, *, read_password=read_password_securely, environ=None, out=None) -> int:
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    if environ is None:
        from dotenv import load_dotenv

        load_dotenv()  # same configuration source as the application
        environ = os.environ
    try:
        if args.role == "admin" and not args.confirm_admin:
            raise ProvisioningError(
                "creating an admin requires --confirm-admin", EXIT_INVALID_ARGUMENTS
            )
        validate_identity(args.username, args.email)
        check_target(args.database, environ)
        out.write(
            f"Target: database {environ['DB_NAME']} on {environ['DB_HOST']}:{environ['DB_PORT']}"
            f" as {environ['DB_USER']}. Creating {args.role} user {args.username}.\n"
        )
        password = obtain_password(read_password)
        user = persist(args.username, args.email, password, args.role)
    except ProvisioningError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return exc.exit_code
    except KeyboardInterrupt, EOFError:
        sys.stderr.write("\nInterrupted; nothing was changed.\n")
        return EXIT_INTERRUPTED
    except psycopg.Error as exc:
        sys.stderr.write(f"Error: unexpected database error (SQLSTATE {exc.sqlstate})\n")
        return EXIT_UNEXPECTED
    out.write(f"Created {user['role']} user {user['username']} (id {user['id']}).\n")
    return EXIT_CREATED


if __name__ == "__main__":
    sys.exit(main())
