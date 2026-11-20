"""Unit tests for the test-database safety checks. No database access."""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from tests.db_safety import (
    CONNECTION_KEYS,
    OWNERSHIP_MARKER,
    CreatedDatabase,
    FoundDatabase,
    UnsafeTestDatabase,
    apply_environment,
    check_identity,
    check_role,
    cleanup_targets,
    is_disposable_name,
    is_session_database,
    new_database_name,
    read_test_env_file,
    resolve_settings,
    validate_settings,
)

# Placeholder values only; not real credentials.
VALID = {
    "MEDIBRIDGE_ENV": "test",
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_USER": "placeholder_user",
    "DB_PASSWORD": "placeholder-password",
    "JWT_SECRET_KEY": "x" * 64,
}


# --- configuration -----------------------------------------------------------


def test_valid_settings_are_accepted():
    config = validate_settings(VALID)
    assert (config.host, config.port, config.maintenance_db) == ("localhost", 5432, "postgres")
    assert config.allow_superuser is False


@pytest.mark.parametrize("value", [None, "", "development", "production", "TEST"])
def test_missing_or_wrong_environment_marker_is_rejected(value):
    settings = (
        {**VALID, "MEDIBRIDGE_ENV": value}
        if value is not None
        else {k: v for k, v in VALID.items() if k != "MEDIBRIDGE_ENV"}
    )
    with pytest.raises(UnsafeTestDatabase, match="MEDIBRIDGE_ENV"):
        validate_settings(settings)


@pytest.mark.parametrize("key", ["DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "JWT_SECRET_KEY"])
def test_each_missing_required_setting_is_named(key):
    settings = {k: v for k, v in VALID.items() if k != key}
    with pytest.raises(UnsafeTestDatabase, match=key):
        validate_settings(settings)


def test_error_messages_never_contain_secret_values():
    settings = {k: v for k, v in VALID.items() if k != "DB_USER"}
    with pytest.raises(UnsafeTestDatabase) as caught:
        validate_settings(settings)
    assert "placeholder-password" not in str(caught.value)
    assert "x" * 64 not in str(caught.value)


@pytest.mark.parametrize("host", ["db.example.com", "10.0.0.5", "prod-db", "localhost.example.com"])
def test_non_allowed_host_is_rejected(host):
    with pytest.raises(UnsafeTestDatabase, match="not an allowed test database host"):
        validate_settings({**VALID, "DB_HOST": host})


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "127.0.0.1", "::1"])
def test_loopback_hosts_are_allowed_by_default(host):
    assert validate_settings({**VALID, "DB_HOST": host}).host == host


def test_allowed_hosts_can_be_set_explicitly():
    settings = {**VALID, "DB_HOST": "postgres", "TEST_DB_ALLOWED_HOSTS": "postgres, localhost"}
    assert validate_settings(settings).host == "postgres"
    with pytest.raises(UnsafeTestDatabase):
        validate_settings({**settings, "DB_HOST": "127.0.0.1"})


def test_non_numeric_port_is_rejected():
    with pytest.raises(UnsafeTestDatabase, match="DB_PORT"):
        validate_settings({**VALID, "DB_PORT": "fivefourthreetwo"})


def test_maintenance_database_cannot_be_a_disposable_database():
    with pytest.raises(UnsafeTestDatabase, match="TEST_DB_MAINTENANCE_DB"):
        validate_settings({**VALID, "TEST_DB_MAINTENANCE_DB": new_database_name()})


# --- where settings come from (N1, N3) ----------------------------------------------


def test_connection_settings_come_only_from_the_test_env_file_when_it_exists():
    file_values = {k: v for k, v in VALID.items() if k != "DB_PASSWORD"}
    shell = {"DB_PASSWORD": "value-from-shell", "DB_HOST": "dev-host"}

    settings = resolve_settings(file_values, shell)

    assert "DB_PASSWORD" not in settings  # not filled in from the shell
    assert settings["DB_HOST"] == "localhost"
    with pytest.raises(UnsafeTestDatabase, match="DB_PASSWORD"):
        validate_settings(settings)


def test_process_environment_is_used_when_there_is_no_test_env_file():
    assert resolve_settings(None, VALID)["DB_USER"] == "placeholder_user"


def test_harness_flags_may_come_from_the_shell():
    file_values = {k: v for k, v in VALID.items() if k != "MEDIBRIDGE_ENV"}
    settings = resolve_settings(
        file_values, {"MEDIBRIDGE_ENV": "test", "TEST_DB_ALLOWED_HOSTS": "localhost"}
    )
    assert validate_settings(settings).host == "localhost"


def test_db_name_from_configuration_is_never_used():
    settings = resolve_settings({**VALID, "DB_NAME": "medibridge"}, {})
    assert "DB_NAME" not in validate_settings(settings).app_settings


def test_superuser_override_is_accepted_from_the_test_env_file():
    settings = resolve_settings({**VALID, "TEST_DB_ALLOW_SUPERUSER": "1"}, {})
    assert validate_settings(settings).allow_superuser is True


def test_superuser_override_requires_exactly_1():
    settings = resolve_settings({**VALID, "TEST_DB_ALLOW_SUPERUSER": "true"}, {})
    assert validate_settings(settings).allow_superuser is False


@pytest.mark.parametrize(
    "file_values", [VALID, None], ids=["with-test-env-file", "ci-without-file"]
)
def test_superuser_override_from_the_environment_is_rejected(file_values):
    shell = {**VALID, "TEST_DB_ALLOW_SUPERUSER": "1"}
    with pytest.raises(UnsafeTestDatabase, match="only accepted from the local .env.test"):
        resolve_settings(file_values, shell)


def test_without_a_test_env_file_the_superuser_override_is_impossible():
    # CI has no .env.test, so nothing can turn the override on there.
    settings = resolve_settings(None, VALID)
    assert validate_settings(settings).allow_superuser is False


def _apply(file_values, environ):
    config = validate_settings(resolve_settings(file_values, environ))
    apply_environment(environ, config, "medibridge_test_0123456789abcdef")
    return environ


def test_optional_settings_omitted_from_the_file_are_removed_not_inherited():
    shell = {
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "1",
        "JWT_ALGORITHM": "none",
        "DB_NAME": "medibridge",
        "UNRELATED": "kept",
    }

    environ = _apply(dict(VALID), shell)

    # The application falls back to its own coded defaults, not shell values.
    assert "JWT_ACCESS_TOKEN_EXPIRE_MINUTES" not in environ
    assert "JWT_ALGORITHM" not in environ
    assert environ["DB_NAME"] == "medibridge_test_0123456789abcdef"
    assert environ["UNRELATED"] == "kept"


def test_optional_settings_defined_in_the_file_are_used():
    file_values = {**VALID, "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "30"}
    environ = _apply(file_values, {"JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "1"})
    assert environ["JWT_ACCESS_TOKEN_EXPIRE_MINUTES"] == "30"


def test_every_connection_setting_ends_up_from_the_file_or_absent():
    shell = {key: "from-shell" for key in CONNECTION_KEYS}
    environ = _apply(dict(VALID), shell)
    for key in CONNECTION_KEYS:
        assert environ.get(key) == VALID.get(key), key


def test_apply_environment_disables_dotenv_and_sets_the_marker():
    environ = _apply(dict(VALID), {})
    assert environ["PYTHON_DOTENV_DISABLED"] == "1"
    assert environ["MEDIBRIDGE_ENV"] == "test"


def test_test_env_file_values_are_not_interpolated_from_the_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env.test"
    env_file.write_text(
        "DB_PASSWORD=${SHELL_SECRET}\nDB_USER=tester\nEMPTY_KEY\n", encoding="utf-8"
    )
    monkeypatch.setenv("SHELL_SECRET", "leaked-from-shell")

    values = read_test_env_file(env_file)

    assert values["DB_PASSWORD"] == "${SHELL_SECRET}"
    assert values["DB_USER"] == "tester"
    assert "EMPTY_KEY" not in values


def test_missing_test_env_file_reads_as_none(tmp_path):
    assert read_test_env_file(tmp_path / "absent.env") is None


def test_disabled_dotenv_does_not_load_a_development_env_file(tmp_path, monkeypatch):
    dev_env = tmp_path / ".env"
    dev_env.write_text("MEDIBRIDGE_DOTENV_PROBE=from-development-env\n", encoding="utf-8")
    monkeypatch.delenv("MEDIBRIDGE_DOTENV_PROBE", raising=False)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")

    assert load_dotenv(dev_env) is False
    assert "MEDIBRIDGE_DOTENV_PROBE" not in os.environ


def test_the_running_session_has_dotenv_disabled():
    assert os.environ.get("PYTHON_DOTENV_DISABLED") == "1"


# --- database names and session identity --------------------------------------------


def test_generated_names_are_disposable_and_unique():
    names = {new_database_name() for _ in range(200)}
    assert len(names) == 200
    assert all(is_disposable_name(name) for name in names)


@pytest.mark.parametrize(
    "name",
    [
        "medibridge",
        "medibridge_test",
        "medibridge_ci_test",
        "postgres",
        "medibridge_test_",
        "medibridge_test_0123456789abcdef0",
        "medibridge_test_0123456789ABCDEF",
        "medibridge_test_0123456789abcdeg",
        "xmedibridge_test_0123456789abcdef",
    ],
)
def test_existing_and_malformed_names_are_not_disposable(name):
    assert not is_disposable_name(name)


def test_only_this_sessions_database_counts_as_the_session_database():
    current = os.environ["DB_NAME"]
    assert is_session_database(current)
    assert not is_session_database(new_database_name())  # well-formed, but another session's
    assert not is_session_database("medibridge_test")
    assert not is_session_database("medibridge")


# --- server role ----------------------------------------------------------------------


def test_least_privileged_role_is_accepted():
    check_role(is_superuser=False, can_create_db=True, in_recovery=False, allow_superuser=False)


def test_superuser_is_rejected_unless_explicitly_allowed():
    with pytest.raises(UnsafeTestDatabase, match="superuser"):
        check_role(is_superuser=True, can_create_db=True, in_recovery=False, allow_superuser=False)
    check_role(is_superuser=True, can_create_db=True, in_recovery=False, allow_superuser=True)


def test_role_without_createdb_is_rejected():
    with pytest.raises(UnsafeTestDatabase, match="CREATEDB"):
        check_role(
            is_superuser=False, can_create_db=False, in_recovery=False, allow_superuser=False
        )


def test_replica_is_rejected():
    with pytest.raises(UnsafeTestDatabase, match="replica"):
        check_role(is_superuser=False, can_create_db=True, in_recovery=True, allow_superuser=False)


# --- identity before truncate or drop (N2) ----------------------------------------------

SESSION = "medibridge_test_0123456789abcdef"
ROLE = 16384
CREATED = CreatedDatabase(name=SESSION, oid=50001, owner_oid=ROLE)
MARKED = FoundDatabase(oid=50001, owner_oid=ROLE, comment=OWNERSHIP_MARKER)
UNMARKED = FoundDatabase(oid=50001, owner_oid=ROLE, comment=None)


def _identity(created=CREATED, found=MARKED, role=ROLE, require_marker=True, session=SESSION):
    check_identity(
        session_name=session,
        created=created,
        found=found,
        current_role_oid=role,
        require_marker=require_marker,
    )


def test_marked_database_created_by_this_process_passes():
    _identity()


def test_unmarked_database_passes_only_when_this_process_never_wrote_the_marker():
    _identity(found=UNMARKED, require_marker=False)
    with pytest.raises(UnsafeTestDatabase, match="ownership marker"):
        _identity(found=UNMARKED, require_marker=True)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"session": "medibridge_test"}, "not a disposable"),
        ({"session": "medibridge"}, "not a disposable"),
        ({"created": None}, "ownership cannot be established"),
        (
            {
                "created": CreatedDatabase(
                    name="medibridge_test_ffffffffffffffff", oid=50001, owner_oid=ROLE
                )
            },
            "not this session",
        ),
        ({"found": None}, "no longer exists"),
        (
            {"found": FoundDatabase(oid=50002, owner_oid=ROLE, comment=OWNERSHIP_MARKER)},
            "OID changed",
        ),
        ({"found": FoundDatabase(oid=50001, owner_oid=99, comment=OWNERSHIP_MARKER)}, "not owned"),
        ({"role": 99}, "not owned"),
        ({"found": FoundDatabase(oid=50001, owner_oid=ROLE, comment="other")}, "ownership marker"),
        (
            {
                "found": FoundDatabase(oid=50001, owner_oid=ROLE, comment="other"),
                "require_marker": False,
            },
            "unexpected comment",
        ),
        (
            {
                "found": FoundDatabase(oid=50002, owner_oid=ROLE, comment=None),
                "require_marker": False,
            },
            "OID changed",
        ),
    ],
)
def test_anything_else_is_refused(kwargs, reason):
    with pytest.raises(UnsafeTestDatabase, match=reason):
        _identity(**kwargs)


# --- cleanup scope ------------------------------------------------------------------------


def test_cleanup_targets_are_public_tables_except_migration_metadata():
    rows = [("public", "patients"), ("public", "schema_migrations"), ("public", "audit_logs")]
    assert cleanup_targets(rows) == ["audit_logs", "patients"]


def test_tables_outside_the_supported_schema_stop_the_run():
    rows = [("public", "patients"), ("reporting", "patient_summary")]
    with pytest.raises(UnsafeTestDatabase, match="reporting.patient_summary"):
        cleanup_targets(rows)


def test_no_application_tables_is_an_error():
    with pytest.raises(UnsafeTestDatabase, match="No application tables"):
        cleanup_targets([("public", "schema_migrations")])


def test_root_conftest_guard_exists_for_runs_started_inside_tests():
    guard = Path(__file__).with_name("conftest.py").read_text(encoding="utf-8")
    assert "_medibridge_database_isolation" in guard
    assert 'os.environ["PYTHON_DOTENV_DISABLED"] = "1"' in guard
