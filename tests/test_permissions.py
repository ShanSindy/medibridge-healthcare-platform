import pytest
from fastapi import HTTPException

from auth_dependencies import require_permission
from permissions import get_allowed_roles


def make_user(role):
    return {
        "id": 1,
        "username": "demo_user",
        "email": "demo@example.com",
        "role": role,
        "is_active": True,
    }


def test_admin_can_write_patients():
    checker = require_permission("patients:write")

    user = checker(current_user=make_user("admin"))

    assert user["role"] == "admin"


def test_clinician_can_write_patients():
    checker = require_permission("patients:write")

    user = checker(current_user=make_user("clinician"))

    assert user["role"] == "clinician"


def test_scheduler_cannot_write_patients():
    checker = require_permission("patients:write")

    with pytest.raises(HTTPException) as exc:
        checker(current_user=make_user("scheduler"))

    assert exc.value.status_code == 403


def test_scheduler_can_write_appointments():
    checker = require_permission("appointments:write")

    user = checker(current_user=make_user("scheduler"))

    assert user["role"] == "scheduler"


def test_clinician_cannot_write_appointments():
    checker = require_permission("appointments:write")

    with pytest.raises(HTTPException) as exc:
        checker(current_user=make_user("clinician"))

    assert exc.value.status_code == 403


def test_viewer_can_read_providers():
    checker = require_permission("providers:read")

    user = checker(current_user=make_user("viewer"))

    assert user["role"] == "viewer"


def test_viewer_cannot_read_patients():
    checker = require_permission("patients:read")

    with pytest.raises(HTTPException) as exc:
        checker(current_user=make_user("viewer"))

    assert exc.value.status_code == 403


def test_only_admin_can_manage_users():
    checker = require_permission("users:manage")

    assert checker(current_user=make_user("admin"))["role"] == "admin"

    with pytest.raises(HTTPException) as exc:
        checker(current_user=make_user("scheduler"))

    assert exc.value.status_code == 403


def test_unknown_permission_rejected():
    with pytest.raises(ValueError):
        require_permission("unknown:permission")


def test_permission_configuration():
    assert get_allowed_roles("patients:write") == (
        "admin",
        "clinician",
    )

    assert get_allowed_roles("appointments:write") == (
        "admin",
        "scheduler",
    )


import pytest

from permissions import get_allowed_roles


def test_audit_read_permission_is_admin_only():
    allowed_roles = get_allowed_roles("audit:read")

    assert allowed_roles == ("admin",)

    assert "clinician" not in allowed_roles
    assert "scheduler" not in allowed_roles
    assert "viewer" not in allowed_roles


def test_unknown_audit_permission_is_rejected():
    with pytest.raises(ValueError, match="Unknown permission"):
        get_allowed_roles("audit:write")
