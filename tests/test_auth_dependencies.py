import secrets

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

import auth_dependencies
import main
from auth_dependencies import get_current_user
from database import get_connection
from tests import db_safety
from token_service import create_access_token
from user_service import create_user


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


def test_http_bearer_rejects_a_valid_token_sent_with_another_scheme(
    disposable_database, monkeypatch
):
    """
    FastAPI's HTTPBearer rejects a non-Bearer scheme before any user lookup.
    get_current_user does not inspect the scheme itself. The same token, for an
    existing active user, works with Bearer before and after the Basic request.
    """
    with get_connection() as conn:
        found, role_oid = db_safety.fetch_identity(conn, disposable_database.name)
        db_safety.check_identity(
            session_name=disposable_database.name,
            created=disposable_database.created,
            found=found,
            current_role_oid=role_oid,
            require_marker=True,
        )
    password = secrets.token_urlsafe(24)
    user = create_user(
        username="synthetic.scheme.viewer",
        email="scheme.viewer@example.org",
        password=password,
        role="viewer",
    )
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides.clear()
    try:
        client = TestClient(main.app)
        login = client.post(
            "/auth/login", data={"username": user["username"], "password": password}
        )
        token = login.json()["access_token"]
        assert (
            client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        )

        # Observation only: records user lookups and delegates to the real function.
        lookups = []
        real_get_user_by_id = auth_dependencies.get_user_by_id

        def observed_get_user_by_id(user_id):
            lookups.append(user_id)
            return real_get_user_by_id(user_id)

        monkeypatch.setattr(auth_dependencies, "get_user_by_id", observed_get_user_by_id)

        rejected = client.get("/auth/me", headers={"Authorization": f"Basic {token}"})

        assert rejected.status_code == 401
        assert rejected.json() == {"detail": "Invalid or missing authentication credentials"}
        assert rejected.headers["www-authenticate"] == "Bearer"
        assert lookups == []  # rejected before the user was looked up

        accepted = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert accepted.status_code == 200
        assert lookups == [user["id"]]
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)
