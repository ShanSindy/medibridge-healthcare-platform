import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from auth_dependencies import get_current_user
from token_service import create_access_token


def make_credentials(token):
    return HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=token,
    )


def test_get_current_user_valid_token(monkeypatch):
    token = create_access_token(1)

    def fake_get_user_by_id(user_id):
        assert user_id == 1

        return {
            "id": 1,
            "username": "demo_user",
            "email": "demo@example.com",
            "role": "viewer",
            "is_active": True,
        }

    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        fake_get_user_by_id,
    )

    user = get_current_user(make_credentials(token))

    assert user["id"] == 1
    assert user["username"] == "demo_user"
    assert "password_hash" not in user


def test_get_current_user_missing_token():
    with pytest.raises(HTTPException) as exc:
        get_current_user(None)

    assert exc.value.status_code == 401


def test_get_current_user_invalid_token():
    with pytest.raises(HTTPException) as exc:
        get_current_user(make_credentials("invalid-token"))

    assert exc.value.status_code == 401


def test_get_current_user_missing_account(monkeypatch):
    token = create_access_token(999)

    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: None,
    )

    with pytest.raises(HTTPException) as exc:
        get_current_user(make_credentials(token))

    assert exc.value.status_code == 401


def test_get_current_user_disabled_account(monkeypatch):
    token = create_access_token(1)

    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: {
            "id": 1,
            "username": "disabled_user",
            "email": "disabled@example.com",
            "role": "viewer",
            "is_active": False,
        },
    )

    with pytest.raises(HTTPException) as exc:
        get_current_user(make_credentials(token))

    assert exc.value.status_code == 401


def test_get_current_user_rejects_wrong_scheme():
    token = create_access_token(1)

    credentials = HTTPAuthorizationCredentials(
        scheme="Basic",
        credentials=token,
    )

    with pytest.raises(HTTPException) as exc:
        get_current_user(credentials)

    assert exc.value.status_code == 401
    assert exc.value.headers["WWW-Authenticate"] == "Bearer"
