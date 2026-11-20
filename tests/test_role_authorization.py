import pytest
from fastapi import HTTPException

from auth_dependencies import require_roles


def make_user(role):
    return {
        "id": 1,
        "username": "demo_user",
        "email": "demo@example.com",
        "role": role,
        "is_active": True,
    }


def test_admin_allowed():
    checker = require_roles("admin", "scheduler")

    user = checker(current_user=make_user("admin"))

    assert user["role"] == "admin"


def test_scheduler_allowed():
    checker = require_roles("admin", "scheduler")

    user = checker(current_user=make_user("scheduler"))

    assert user["role"] == "scheduler"


def test_viewer_rejected():
    checker = require_roles("admin", "scheduler")

    with pytest.raises(HTTPException) as exc:
        checker(current_user=make_user("viewer"))

    assert exc.value.status_code == 403
    assert exc.value.detail == "Insufficient permissions"


def test_clinician_rejected():
    checker = require_roles("admin", "scheduler")

    with pytest.raises(HTTPException) as exc:
        checker(current_user=make_user("clinician"))

    assert exc.value.status_code == 403


def test_single_role_allowed():
    checker = require_roles("clinician")

    user = checker(current_user=make_user("clinician"))

    assert user["role"] == "clinician"


def test_no_roles_rejected():
    with pytest.raises(ValueError):
        require_roles()


def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        require_roles("superuser")
