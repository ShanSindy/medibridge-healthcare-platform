"""Unit tests for the test-database safety checks. No database access."""

import pytest

from db_safety import (
    OWNERSHIP_MARKER,
    UnsafeTestDatabase,
    check_ownership,
    check_role,
    is_disposable_name,
    new_database_name,
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
    assert config.app_settings["MEDIBRIDGE_ENV"] == "test"


@pytest.mark.parametrize("value", [None, "", "development", "production", "TEST"])
def test_missing_or_wrong_environment_marker_is_rejected(value):
    settings = {**VALID, "MEDIBRIDGE_ENV": value} if value is not None else {
        k: v for k, v in VALID.items() if k != "MEDIBRIDGE_ENV"
    }
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


def test_superuser_opt_in_requires_exactly_1():
    assert validate_settings({**VALID, "TEST_DB_ALLOW_SUPERUSER": "1"}).allow_superuser is True
    assert validate_settings({**VALID, "TEST_DB_ALLOW_SUPERUSER": "true"}).allow_superuser is False


# --- where settings come from --------------------------------------------------


def test_connection_settings_come_only_from_the_test_env_file_when_it_exists():
    file_values = {**VALID}
    del file_values["DB_PASSWORD"]
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
    settings = resolve_settings(file_values, {"MEDIBRIDGE_ENV": "test", "TEST_DB_ALLOW_SUPERUSER": "1"})
    assert validate_settings(settings).allow_superuser is True


def test_db_name_from_configuration_is_never_used():
    settings = resolve_settings({**VALID, "DB_NAME": "medibridge"}, {})
    assert "DB_NAME" not in validate_settings(settings).app_settings


# --- database names ------------------------------------------------------------


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


# --- server role -----------------------------------------------------------------


def test_least_privileged_role_is_accepted():
    check_role(is_superuser=False, can_create_db=True, in_recovery=False, allow_superuser=False)


def test_superuser_is_rejected_unless_explicitly_allowed():
    with pytest.raises(UnsafeTestDatabase, match="superuser"):
        check_role(is_superuser=True, can_create_db=True, in_recovery=False, allow_superuser=False)
    check_role(is_superuser=True, can_create_db=True, in_recovery=False, allow_superuser=True)


def test_role_without_createdb_is_rejected():
    with pytest.raises(UnsafeTestDatabase, match="CREATEDB"):
        check_role(is_superuser=False, can_create_db=False, in_recovery=False, allow_superuser=False)


def test_replica_is_rejected():
    with pytest.raises(UnsafeTestDatabase, match="replica"):
        check_role(is_superuser=False, can_create_db=True, in_recovery=True, allow_superuser=False)


# --- ownership before truncate or drop --------------------------------------------

SESSION = "medibridge_test_0123456789abcdef"


def test_owned_session_database_passes():
    check_ownership(expected_name=SESSION, found_name=SESSION, comment=OWNERSHIP_MARKER, owned_by_current_role=True)


@pytest.mark.parametrize(
    ("expected_name", "found_name", "comment", "owned", "reason"),
    [
        ("medibridge_test", "medibridge_test", OWNERSHIP_MARKER, True, "not a disposable"),
        ("medibridge", "medibridge", OWNERSHIP_MARKER, True, "not a disposable"),
        (SESSION, None, OWNERSHIP_MARKER, True, "not this session"),
        (SESSION, "medibridge_test_ffffffffffffffff", OWNERSHIP_MARKER, True, "not this session"),
        (SESSION, SESSION, None, True, "ownership marker"),
        (SESSION, SESSION, "some other comment", True, "ownership marker"),
        (SESSION, SESSION, OWNERSHIP_MARKER, False, "not owned"),
        (SESSION, SESSION, OWNERSHIP_MARKER, None, "not owned"),
    ],
)
def test_anything_else_is_refused(expected_name, found_name, comment, owned, reason):
    with pytest.raises(UnsafeTestDatabase, match=reason):
        check_ownership(
            expected_name=expected_name,
            found_name=found_name,
            comment=comment,
            owned_by_current_role=owned,
        )
