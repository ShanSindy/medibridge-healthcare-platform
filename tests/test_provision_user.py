"""
provision_user.py: argument checks, password handling, exit codes, and real user creation
in this session's disposable database (in-process). Child-process runs of the actual entry
point never connect to a database: they run from an empty directory with .env loading
disabled and placeholder settings for a closed port.
"""

import getpass
import io
import subprocess
import sys
import warnings
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import errors

import main as app_main
import provision_user
from security import verify_password
from tests import db_safety

SCRIPT = Path(__file__).resolve().parents[1] / "provision_user.py"
PASSWORD = "synthetic-Provisioning-passphrase-42"


@pytest.fixture
def db(disposable_database):
    opened = []

    def connect():
        conn = psycopg.connect(
            **disposable_database.config.connect_kwargs(disposable_database.name),
            autocommit=True,
        )
        opened.append(conn)
        found, role_oid = db_safety.fetch_identity(conn, disposable_database.name)
        db_safety.check_identity(
            session_name=disposable_database.name,
            created=disposable_database.created,
            found=found,
            current_role_oid=role_oid,
            require_marker=True,
        )
        return conn

    yield connect
    for conn in opened:
        conn.close()


def args(database, *extra, username="synthetic.operator", role="clinician"):
    return [
        "--database",
        database,
        "--username",
        username,
        "--email",
        f"{username}@example.org",
        "--role",
        role,
        *extra,
    ]


def answers(*values):
    calls = []

    def read(prompt):
        calls.append(prompt)
        value = values[len(calls) - 1]
        if isinstance(value, BaseException):
            raise value
        return value

    read.calls = calls
    return read


def never_called(prompt):
    raise AssertionError("the password must not be requested")


def user_count(db):
    return db().execute("SELECT count(*) FROM users").fetchone()[0]


# --- arguments, intent and configuration (no writes) -----------------------------------


def test_help_runs_through_the_real_entry_point_and_lists_roles_and_exit_codes(tmp_path):
    # Trusted input: this interpreter and the repository's own script.
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"PYTHON_DOTENV_DISABLED": "1", "SYSTEMROOT": _systemroot()},
        timeout=60,
        check=False,
    )

    assert result.returncode == 0
    for role in ("admin", "clinician", "scheduler", "viewer"):
        assert role in result.stdout
    assert "--confirm-admin" in result.stdout
    assert "Exit codes" in result.stdout


@pytest.mark.parametrize(
    "argv",
    [
        ["--database", "x", "--username", "u", "--email", "u@example.org", "--role", "owner"],
        ["--database", "x", "--username", "u", "--role", "viewer"],
    ],
    ids=["unknown-role", "missing-email"],
)
def test_invalid_arguments_exit_2(argv):
    with pytest.raises(SystemExit) as raised:
        provision_user.main(argv, read_password=never_called, environ={})
    assert raised.value.code == 2


def test_admin_requires_explicit_confirmation(disposable_database, db):
    code = provision_user.main(
        args(disposable_database.name, role="admin"), read_password=never_called, environ=_env()
    )

    assert code == provision_user.EXIT_INVALID_ARGUMENTS
    assert user_count(db) == 0


@pytest.mark.parametrize(
    ("username", "email"),
    [("has space", "a@example.org"), ("x" * 51, "a@example.org"), ("ok", "not-an-email")],
    ids=["whitespace", "too-long", "bad-email"],
)
def test_invalid_username_or_email_exit_2(disposable_database, username, email):
    argv = ["--database", disposable_database.name, "--username", username]
    argv += ["--email", email, "--role", "viewer"]

    assert provision_user.main(argv, read_password=never_called, environ=_env()) == 2


def test_target_must_match_configured_database_and_settings_must_exist(disposable_database):
    mismatch = provision_user.main(args("medibridge"), read_password=never_called, environ=_env())
    missing = provision_user.main(
        args(disposable_database.name), read_password=never_called, environ={}
    )

    assert mismatch == missing == provision_user.EXIT_CONFIGURATION


# --- password handling ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("replies", "expected"),
    [
        ((PASSWORD, PASSWORD + "x"), provision_user.EXIT_PASSWORD_REJECTED),
        (("too-short",), provision_user.EXIT_PASSWORD_REJECTED),
        ((KeyboardInterrupt(),), provision_user.EXIT_INTERRUPTED),
        ((PASSWORD, EOFError()), provision_user.EXIT_INTERRUPTED),
    ],
    ids=["mismatch", "too-short", "interrupted", "eof-at-confirmation"],
)
def test_rejected_or_interrupted_password_entry_writes_nothing(
    disposable_database, db, capsys, replies, expected
):
    code = provision_user.main(
        args(disposable_database.name), read_password=answers(*replies), environ=_env()
    )

    assert code == expected
    assert user_count(db) == 0
    output = capsys.readouterr()
    assert PASSWORD not in output.out + output.err


def test_password_is_not_read_without_a_terminal(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))
    monkeypatch.setattr(getpass, "getpass", never_called)

    with pytest.raises(provision_user.ProvisioningError) as raised:
        provision_user.read_password_securely("Password: ")
    assert raised.value.exit_code == provision_user.EXIT_PASSWORD_REJECTED


def test_getpass_fallback_to_visible_input_is_refused(monkeypatch):
    class Terminal(io.StringIO):
        def isatty(self):
            return True

    def echoing_getpass(prompt):
        warnings.warn("Can not control echo on the terminal.", getpass.GetPassWarning, stacklevel=1)
        return "typed-while-visible"

    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr(provision_user, "stdin_is_interactive", lambda: True)
    monkeypatch.setattr(getpass, "getpass", echoing_getpass)

    with pytest.raises(provision_user.ProvisioningError, match="cannot hide"):
        provision_user.read_password_securely("Password: ")


def test_real_entry_point_without_a_terminal_refuses_before_connecting(tmp_path):
    env = {
        "PYTHON_DOTENV_DISABLED": "1",
        "SYSTEMROOT": _systemroot(),
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "9",
        "DB_NAME": "placeholder_db",
        "DB_USER": "placeholder_user",
        "DB_PASSWORD": "placeholder-not-a-secret",
    }
    # Trusted input: this interpreter and the repository's own script.
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *args("placeholder_db")],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )

    assert result.returncode == provision_user.EXIT_PASSWORD_REJECTED
    assert "interactive terminal" in result.stderr
    assert "placeholder-not-a-secret" not in result.stdout + result.stderr


# --- real persistence ------------------------------------------------------------------


def test_provisioned_user_is_stored_securely_and_can_sign_in(disposable_database, db, capsys):
    db()  # proves the session database before the write
    code = provision_user.main(
        args(disposable_database.name, username="synthetic.provisioned"),
        read_password=answers(PASSWORD, PASSWORD),
        environ=_env(),
    )

    assert code == provision_user.EXIT_CREATED
    output = capsys.readouterr()
    assert "Created clinician user synthetic.provisioned" in output.out
    assert PASSWORD not in output.out + output.err
    username, email, role, active, password_hash = (
        db().execute("SELECT username, email, role, is_active, password_hash FROM users").fetchone()
    )
    assert (username, email, role, active) == (
        "synthetic.provisioned",
        "synthetic.provisioned@example.org",
        "clinician",
        True,
    )
    assert password_hash.startswith("$argon2id$")
    assert PASSWORD not in password_hash
    assert password_hash not in output.out + output.err
    assert verify_password(PASSWORD, password_hash)
    assert not verify_password(PASSWORD + "x", password_hash)

    previous = dict(app_main.app.dependency_overrides)
    app_main.app.dependency_overrides.clear()
    try:
        client = TestClient(app_main.app)
        login = client.post(
            "/auth/login", data={"username": "synthetic.provisioned", "password": PASSWORD}
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        wrong = client.post(
            "/auth/login", data={"username": "synthetic.provisioned", "password": "wrong-pass"}
        )
    finally:
        app_main.app.dependency_overrides.clear()
        app_main.app.dependency_overrides.update(previous)
    assert (me.status_code, me.json()["role"]) == (200, "clinician")
    assert wrong.status_code == 401


def test_admin_is_created_only_with_confirmation(disposable_database, db):
    db()
    code = provision_user.main(
        args(disposable_database.name, "--confirm-admin", username="synthetic.admin", role="admin"),
        read_password=answers(PASSWORD, PASSWORD),
        environ=_env(),
    )

    assert code == provision_user.EXIT_CREATED
    assert db().execute("SELECT role FROM users").fetchone()[0] == "admin"


@pytest.mark.parametrize("duplicate", ["username", "email"])
def test_duplicates_are_rejected_and_the_existing_user_is_unchanged(
    disposable_database, db, capsys, duplicate
):
    db()
    first = args(disposable_database.name, username="synthetic.first")
    assert (
        provision_user.main(first, read_password=answers(PASSWORD, PASSWORD), environ=_env()) == 0
    )
    before = db().execute("SELECT id, username, email, role, password_hash FROM users").fetchall()
    capsys.readouterr()

    second = args(disposable_database.name, username="synthetic.second")
    if duplicate == "username":
        second[3] = "synthetic.first"
    else:
        second[5] = "synthetic.first@example.org"
    other_password = PASSWORD + "-other"
    code = provision_user.main(
        second, read_password=answers(other_password, other_password), environ=_env()
    )

    assert code == provision_user.EXIT_ALREADY_EXISTS
    assert f"a user with this {'username' if duplicate == 'username' else 'email'}" in (
        capsys.readouterr().err
    )
    after = db().execute("SELECT id, username, email, role, password_hash FROM users").fetchall()
    assert after == before


# --- database failures -------------------------------------------------------------------


def test_unreachable_database_is_reported_without_details(disposable_database, monkeypatch, capsys):
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "9")  # nothing listens here
    monkeypatch.setenv("PGCONNECT_TIMEOUT", "5")
    code = provision_user.main(
        args(disposable_database.name),
        read_password=answers(PASSWORD, PASSWORD),
        environ=_env(),
    )

    assert code == provision_user.EXIT_DATABASE_UNAVAILABLE
    err = capsys.readouterr().err
    assert "could not connect" in err
    assert PASSWORD not in err


@pytest.mark.parametrize(
    ("error", "expected", "message"),
    [
        (errors.UndefinedTable("users"), provision_user.EXIT_DATABASE_UNAVAILABLE, "migrate.py"),
        (errors.UniqueViolation("other"), provision_user.EXIT_UNEXPECTED, "unexpected database"),
    ],
    ids=["schema-missing", "unrelated-unique-violation"],
)
def test_other_database_errors_are_mapped_or_reported_as_unexpected(
    disposable_database, monkeypatch, capsys, error, expected, message
):
    import user_service

    def fail(**kwargs):
        raise error

    monkeypatch.setattr(user_service, "create_user", fail)
    code = provision_user.main(
        args(disposable_database.name), read_password=answers(PASSWORD, PASSWORD), environ=_env()
    )

    assert code == expected
    assert message in capsys.readouterr().err


# --- helpers ---------------------------------------------------------------------------


def _env():
    import os

    return os.environ


def _systemroot():
    import os

    return os.environ.get("SYSTEMROOT", "")
