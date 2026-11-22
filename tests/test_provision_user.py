"""
provision_user.py: argument checks, password handling, exit codes, and real user creation
in this session's disposable database (in-process). Child-process runs of the actual entry
point never connect to a database: they run from an empty directory with .env loading
disabled and placeholder settings for a closed port.
"""

import contextlib
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


# --- C1: identity validation (CLI-level, not an application-wide policy) ------------------


@pytest.mark.parametrize(
    "email",
    [
        "a@example.org",
        "first.last@sub.example.org",
        "x+tag@example.co",
        "o'neil@xn--bcher-kva.example",
    ],
)
def test_reasonable_email_addresses_are_accepted(email):
    assert provision_user.valid_email(email)


@pytest.mark.parametrize(
    "email",
    [
        "a@@example.org",
        "a@b@example.org",
        "@example.org",
        "a@",
        "a@example",
        "a@.example.org",
        "a@example..org",
        "a@-example.org",
        "a@example-.org",
        ".a@example.org",
        "a.@example.org",
        "a..b@example.org",
        "a b@example.org",
        "a\t@example.org",
        "a@exa mple.org",
        "a@example.123",
        "a\x00@example.org",
        "a​@example.org",
        "a@ex_ample.org",
        "a@exämple.org",
        "x" * 65 + "@example.org",
        "a@" + "b" * 64 + ".org",
        "a@" + ("b" * 60 + ".") * 5 + "org",
    ],
)
def test_malformed_email_addresses_are_rejected(email):
    assert not provision_user.valid_email(email)
    with pytest.raises(provision_user.ProvisioningError) as raised:
        provision_user.validate_identity("synthetic.user", email)
    assert raised.value.exit_code == provision_user.EXIT_INVALID_ARGUMENTS


@pytest.mark.parametrize("username", ["a\x1b[31m", "line\nbreak", "tab\there", "zero​width", ""])
def test_usernames_with_control_or_invisible_characters_are_rejected(username):
    with pytest.raises(provision_user.ProvisioningError) as raised:
        provision_user.validate_identity(username, "a@example.org")
    assert raised.value.exit_code == provision_user.EXIT_INVALID_ARGUMENTS


# --- C1: configuration ----------------------------------------------------------------


def test_settings_come_from_the_environment_then_the_env_file_without_mutating_it(
    tmp_path, monkeypatch
):
    env_file = tmp_path / ".env"
    env_file.write_text("DB_NAME=from_file\nDB_HOST=file-host\nDB_USER=file_user\n")
    monkeypatch.delenv("DB_NAME", raising=False)
    monkeypatch.setenv("DB_HOST", "env-host")
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    import os

    before = dict(os.environ)

    settings = provision_user.load_settings(env_file=env_file)

    assert settings["DB_NAME"] == "from_file"  # only in the file
    assert settings["DB_HOST"] == "env-host"  # the environment wins
    assert dict(os.environ) == before  # nothing was loaded into the process

    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    assert "DB_NAME" not in provision_user.load_settings(env_file=env_file)
    assert provision_user.load_settings({"DB_NAME": "given"}, env_file=env_file) == {
        "DB_NAME": "given"
    }


def test_default_settings_path_is_used_when_nothing_is_injected(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DB_HOST=127.0.0.1\nDB_PORT=9\nDB_NAME=file_db\nDB_USER=file_user\n"
        "DB_PASSWORD=placeholder-not-a-secret\n"
    )
    for name in provision_user.REQUIRED_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    monkeypatch.setattr(provision_user, "DEFAULT_ENV_FILE", env_file)

    code = provision_user.main(args("other_db"), read_password=never_called)

    assert code == provision_user.EXIT_CONFIGURATION
    assert "does not match the configured DB_NAME" in capsys.readouterr().err


def test_entry_point_runs_main(monkeypatch, capsys):
    import runpy

    monkeypatch.setattr(sys, "argv", ["provision_user.py", "--help"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert raised.value.code == 0
    assert "Exit codes" in capsys.readouterr().out


# --- C1: the connection that writes is the connection that was verified ----------------


def test_connected_database_mismatch_writes_nothing(disposable_database, db, monkeypatch, capsys):
    """
    The validated name (DB_NAME == --database) is not the database actually reached, as
    with a connection-pooler alias that maps one name to another database. Simulated by
    connecting to this session's disposable database while the configured name differs;
    the check on that real connection refuses before any insert.
    """
    import dataclasses

    environ = dict(_env())
    environ["DB_NAME"] = "synthetic_alias"
    real_open = provision_user.open_connection
    monkeypatch.setattr(
        provision_user,
        "open_connection",
        lambda target: real_open(dataclasses.replace(target, dbname=disposable_database.name)),
    )

    code = provision_user.main(
        args("synthetic_alias"), read_password=answers(PASSWORD, PASSWORD), environ=environ
    )

    assert code == provision_user.EXIT_CONFIGURATION
    err = capsys.readouterr().err
    assert f"connected database is {disposable_database.name}" in err
    assert "no user was created" in err
    assert user_count(db) == 0


class FakeInfo:
    host = "db.example.internal"
    port = 5432


class FakeIdentityConnection:
    def __init__(self, row, port=5432):
        self.row = row
        self.info = FakeInfo()
        self.info.port = port

    def execute(self, query):
        class Result:
            def fetchone(inner):
                return self.row

        return Result()


@pytest.mark.parametrize(
    ("row", "port", "problem"),
    [
        (("medibridge_demo", "other_role", "other_role"), 5432, "connected role is other_role"),
        (("medibridge_demo", "operator", "superuser"), 5432, "connected role is operator"),
        (("medibridge_demo", "operator", "operator"), 6543, "connected port is 6543"),
        (("medibridge", "operator", "operator"), 5432, "connected database is medibridge"),
    ],
    ids=["role", "session-role", "port", "database"],
)
def test_connected_identity_mismatches_are_refused(row, port, problem):
    target = provision_user.Target(
        "db.example.internal", "5432", "medibridge_demo", "operator", "x"
    )

    with pytest.raises(provision_user.ProvisioningError) as raised:
        provision_user.verify_connected_target(
            FakeIdentityConnection(row, port), target, "medibridge_demo"
        )
    assert raised.value.exit_code == provision_user.EXIT_CONFIGURATION
    assert problem in str(raised.value)


def test_user_is_inserted_on_the_verified_connection(disposable_database, db, monkeypatch):
    import user_service

    opened, used = [], []
    real_open, real_create = provision_user.open_connection, user_service.create_user

    def recording_open(target):
        conn = real_open(target)
        opened.append(conn)
        return conn

    def recording_create(**kwargs):
        used.append(kwargs.get("conn"))
        return real_create(**kwargs)

    monkeypatch.setattr(provision_user, "open_connection", recording_open)
    monkeypatch.setattr(user_service, "create_user", recording_create)

    code = provision_user.main(
        args(disposable_database.name), read_password=answers(PASSWORD, PASSWORD), environ=_env()
    )

    assert code == provision_user.EXIT_CREATED
    assert len(opened) == 1
    assert used == [opened[0]]
    assert opened[0].closed
    assert user_count(db) == 1


def test_create_user_leaves_a_caller_owned_transaction_to_the_caller(disposable_database, db):
    from user_service import create_user

    conn = psycopg.connect(**disposable_database.config.connect_kwargs(disposable_database.name))
    try:
        found, role_oid = db_safety.fetch_identity(conn, disposable_database.name)
        db_safety.check_identity(
            session_name=disposable_database.name,
            created=disposable_database.created,
            found=found,
            current_role_oid=role_oid,
            require_marker=True,
        )
        conn.commit()
        create_user("synthetic.rolledback", "rolledback@example.org", PASSWORD, conn=conn)
        assert user_count(db) == 0  # not committed by create_user
        conn.rollback()
        assert user_count(db) == 0
        created = create_user("synthetic.kept", "kept@example.org", PASSWORD, "viewer", conn=conn)
        conn.commit()
    finally:
        conn.close()
    assert created["username"] == "synthetic.kept"
    assert db().execute("SELECT username FROM users").fetchall() == [("synthetic.kept",)]


# --- C1: bounded connection -----------------------------------------------------------


def test_command_session_is_bounded_and_named(disposable_database):
    target = provision_user.resolve_target(
        disposable_database.name, provision_user.load_settings(_env())
    )
    conn = provision_user.open_connection(target)
    try:
        settings = conn.execute(
            "SELECT current_setting('lock_timeout'), current_setting('statement_timeout'), "
            "current_setting('application_name')"
        ).fetchone()
        connect_timeout = conn.info.get_parameters().get("connect_timeout")
    finally:
        conn.close()

    assert settings == ("10s", "30s", "medibridge-provision-user")
    assert connect_timeout == str(provision_user.CONNECT_TIMEOUT_SECONDS)


def test_unavailable_database_message_is_actionable_and_sanitized(disposable_database, capsys):
    environ = dict(_env())
    environ.update(DB_HOST="127.0.0.1", DB_PORT="9")  # nothing listens here
    secret = environ["DB_PASSWORD"]

    code = provision_user.main(
        args(disposable_database.name), read_password=answers(PASSWORD, PASSWORD), environ=environ
    )

    assert code == provision_user.EXIT_DATABASE_UNAVAILABLE
    output = capsys.readouterr()
    assert "could not connect to 127.0.0.1:9" in output.err
    assert "no user was created" in output.err
    for value in (secret, PASSWORD, "password=", "postgresql://"):
        assert value not in output.out + output.err


def test_lock_timeout_on_a_concurrent_insert_reports_and_writes_nothing(
    disposable_database, db, monkeypatch, capsys
):
    """
    A holds an uncommitted user with the same username; the command waits at most the
    (shortened) lock timeout, then reports that nothing was committed.

    The wait is bounded even if the command had no lock timeout: the server ends A's
    session after holder_idle_limit idle in its transaction, which releases the lock.
    """
    holder_idle_limit = "15s"
    monkeypatch.setattr(provision_user, "LOCK_TIMEOUT", "1s")
    holder = psycopg.connect(**disposable_database.config.connect_kwargs(disposable_database.name))
    try:
        found, role_oid = db_safety.fetch_identity(holder, disposable_database.name)
        db_safety.check_identity(
            session_name=disposable_database.name,
            created=disposable_database.created,
            found=found,
            current_role_oid=role_oid,
            require_marker=True,
        )
        holder.execute(f"SET idle_in_transaction_session_timeout = '{holder_idle_limit}'")
        holder.commit()  # session setting; applies to the transaction below
        holder.execute(
            "INSERT INTO users (username, email, password_hash, role) "
            "VALUES ('synthetic.operator', 'held@example.org', 'not-a-hash', 'viewer')"
        )
        code = provision_user.main(
            args(disposable_database.name),
            read_password=answers(PASSWORD, PASSWORD),
            environ=_env(),
        )
    finally:
        with contextlib.suppress(psycopg.OperationalError):  # ended by the server if it waited
            holder.rollback()
        holder.close()

    assert code == provision_user.EXIT_DATABASE_UNAVAILABLE
    err = capsys.readouterr().err
    assert "did not respond within this command's timeouts" in err
    assert "no user was created" in err
    assert user_count(db) == 0


# --- C1: failure and outcome reporting --------------------------------------------------


@pytest.mark.parametrize(
    ("raised", "expected", "message"),
    [
        (RuntimeError("boom"), provision_user.EXIT_UNEXPECTED, "unexpected error (RuntimeError)"),
        (KeyboardInterrupt(), provision_user.EXIT_INTERRUPTED, "interrupted"),
        (errors.CheckViolation("x"), provision_user.EXIT_UNEXPECTED, "SQLSTATE 23514"),
    ],
    ids=["python-error", "interrupt", "database-error"],
)
def test_failures_after_the_insert_but_before_commit_roll_back(
    disposable_database, db, monkeypatch, capsys, raised, expected, message
):
    import user_service

    real_create = user_service.create_user

    def insert_then_fail(**kwargs):
        real_create(**kwargs)  # the row exists inside the transaction
        raise raised

    monkeypatch.setattr(user_service, "create_user", insert_then_fail)

    code = provision_user.main(
        args(disposable_database.name), read_password=answers(PASSWORD, PASSWORD), environ=_env()
    )

    assert code == expected
    err = capsys.readouterr().err
    assert message in err
    assert "no user was created (the transaction was not committed)" in err
    assert user_count(db) == 0


class CommitFails:
    """Wraps a real connection; COMMIT fails as described by `mode`."""

    def __init__(self, conn, mode):
        self._conn, self._mode = conn, mode

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def commit(self):
        if self._mode == "connection-lost":
            self._conn.close()  # the server rolls back; the client cannot know
            raise psycopg.OperationalError("connection lost")
        if self._mode == "interrupt":
            raise KeyboardInterrupt
        raise errors.SerializationFailure("rejected")


@pytest.mark.parametrize(
    ("mode", "expected", "message"),
    [
        (
            "connection-lost",
            provision_user.EXIT_OUTCOME_UNKNOWN,
            "outcome of the commit is unknown",
        ),
        ("interrupt", provision_user.EXIT_OUTCOME_UNKNOWN, "outcome of the commit is unknown"),
        ("server-rejected", provision_user.EXIT_UNEXPECTED, "rejected the commit (SQLSTATE 40001)"),
    ],
)
def test_commit_failures_are_reported_honestly_and_never_retried(
    disposable_database, db, monkeypatch, capsys, mode, expected, message
):
    import user_service

    calls = []
    real_open, real_create = provision_user.open_connection, user_service.create_user
    monkeypatch.setattr(
        provision_user, "open_connection", lambda t: CommitFails(real_open(t), mode)
    )

    def counting_create(**kwargs):
        calls.append(1)
        kwargs["conn"] = kwargs["conn"]._conn
        return real_create(**kwargs)

    monkeypatch.setattr(user_service, "create_user", counting_create)

    code = provision_user.main(
        args(disposable_database.name), read_password=answers(PASSWORD, PASSWORD), environ=_env()
    )

    assert code == expected
    err = capsys.readouterr().err
    assert message in err
    if expected == provision_user.EXIT_OUTCOME_UNKNOWN:
        assert "Do not run the command again" in err
        assert "nothing was changed" not in err
        assert "no user was created" not in err
    assert calls == [1]  # no automatic retry
    assert user_count(db) == 0  # in these simulations the server did not commit


def test_output_escapes_control_characters_from_configuration(capsys):
    environ = {
        "DB_HOST": "db\x1b[2J.example",
        "DB_PORT": "5432",
        "DB_NAME": "demo",
        "DB_USER": "operator\r\nforged line",
        "DB_PASSWORD": "placeholder-not-a-secret",
    }

    code = provision_user.main(args("demo"), read_password=answers("short"), environ=environ)

    assert code == provision_user.EXIT_PASSWORD_REJECTED
    output = capsys.readouterr()
    assert "\x1b" not in output.out + output.err
    assert "\r" not in output.out
    assert "\\x1b[2J" in output.out
    assert "placeholder-not-a-secret" not in output.out + output.err


# --- C1: terminal detection (mocked platform behaviour) ---------------------------------


class FakeStream:
    def __init__(self, tty=True, fileno_error=None):
        self.tty, self.fileno_error = tty, fileno_error

    def isatty(self):
        return self.tty

    def fileno(self):
        if self.fileno_error:
            raise self.fileno_error
        return 0


def _raise(exc):
    def fail(fd):
        raise exc

    return fail


@pytest.mark.parametrize(
    ("stream", "os_name", "console_check", "expected"),
    [
        (None, "posix", None, False),
        (FakeStream(tty=False), "posix", None, False),
        (FakeStream(tty=True), "posix", None, True),
        (FakeStream(tty=True), "nt", lambda fd: True, True),
        (FakeStream(tty=True), "nt", lambda fd: False, False),
        (FakeStream(tty=True), "nt", _raise(OSError("invalid handle")), False),
        (FakeStream(tty=True), "nt", _raise(RuntimeError("ctypes failure")), False),
        (FakeStream(tty=True, fileno_error=ValueError("closed")), "nt", lambda fd: True, False),
    ],
    ids=[
        "no-stdin",
        "pipe",
        "posix-terminal",
        "windows-console",
        "windows-nul",
        "windows-check-oserror",
        "windows-check-other-error",
        "closed-stream",
    ],
)
def test_terminal_detection_mocked(monkeypatch, stream, os_name, console_check, expected):
    monkeypatch.setattr(sys, "stdin", stream)
    assert provision_user.stdin_is_interactive(os_name=os_name, console_check=console_check) is (
        expected
    )


@pytest.mark.skipif(sys.platform != "win32", reason="real Windows console API")
def test_windows_nul_is_not_a_console_real_api():
    import os

    with open(os.devnull, encoding="utf-8") as nul:
        assert nul.isatty()  # the reason the console-mode check exists
        assert provision_user.windows_console_mode_ok(nul.fileno()) is False
        assert provision_user.stdin_is_interactive(nul) is False


def test_secure_prompt_is_used_with_confirmation(monkeypatch):
    prompts = []
    monkeypatch.setattr(provision_user, "stdin_is_interactive", lambda: True)
    monkeypatch.setattr(getpass, "getpass", lambda prompt: prompts.append(prompt) or PASSWORD)

    assert provision_user.obtain_password(provision_user.read_password_securely) == PASSWORD
    assert prompts == ["Password: ", "Confirm password: "]


@pytest.mark.parametrize(
    ("length", "accepted"), [(11, False), (12, True), (1024, True), (1025, False)]
)
def test_password_length_boundaries(length, accepted):
    value = "p" * length
    if accepted:
        assert provision_user.obtain_password(answers(value, value)) == value
    else:
        with pytest.raises(provision_user.ProvisioningError) as raised:
            provision_user.obtain_password(answers(value, value))
        assert raised.value.exit_code == provision_user.EXIT_PASSWORD_REJECTED


# --- helpers ---------------------------------------------------------------------------


def _env():
    import os

    return os.environ


def _systemroot():
    import os

    return os.environ.get("SYSTEMROOT", "")
