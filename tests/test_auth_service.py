from unittest.mock import patch

from auth_service import authenticate_user
from security import hash_password


def make_test_user(is_active=True):
    return {
        "id": 1,
        "username": "demo_user",
        "email": "demo@example.com",
        "password_hash": hash_password("DemoPassword123!"),
        "role": "viewer",
        "is_active": is_active,
    }


def test_authenticate_user_success():
    user = make_test_user()

    with patch(
        "auth_service.get_user_by_username",
        return_value=user,
    ):
        result = authenticate_user(
            "demo_user",
            "DemoPassword123!",
        )

    assert result is not None
    assert result["id"] == 1
    assert result["username"] == "demo_user"
    assert result["role"] == "viewer"
    assert "password_hash" not in result


def test_authenticate_user_wrong_password():
    user = make_test_user()

    with patch(
        "auth_service.get_user_by_username",
        return_value=user,
    ):
        result = authenticate_user(
            "demo_user",
            "WrongPassword",
        )

    assert result is None


def test_authenticate_user_missing_user():
    with patch(
        "auth_service.get_user_by_username",
        return_value=None,
    ):
        result = authenticate_user(
            "nonexistent_user",
            "DemoPassword123!",
        )

    assert result is None


def test_authenticate_user_disabled_account():
    user = make_test_user(is_active=False)

    with patch(
        "auth_service.get_user_by_username",
        return_value=user,
    ):
        result = authenticate_user(
            "demo_user",
            "DemoPassword123!",
        )

    assert result is None
