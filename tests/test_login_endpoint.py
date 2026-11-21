from fastapi.testclient import TestClient

from main import app
from token_service import verify_access_token

client = TestClient(app)


def test_login_success(monkeypatch):
    def fake_authenticate(username, password):
        assert username == "demo_user"
        assert password == "CorrectPassword123!"

        return {
            "id": 1,
            "username": "demo_user",
            "email": "demo@example.com",
            "role": "viewer",
            "is_active": True,
        }

    monkeypatch.setattr(
        "main.authenticate_user",
        fake_authenticate,
    )

    response = client.post(
        "/auth/login",
        data={
            "username": "demo_user",
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 1800
    assert isinstance(data["access_token"], str)

    claims = verify_access_token(data["access_token"])

    assert claims is not None
    assert claims["sub"] == "1"
    assert claims["type"] == "access"


def test_login_wrong_password(monkeypatch):
    monkeypatch.setattr(
        "main.authenticate_user",
        lambda username, password: None,
    )

    response = client.post(
        "/auth/login",
        data={
            "username": "demo_user",
            "password": "WrongPassword",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == ("Incorrect username or password")


def test_login_nonexistent_user(monkeypatch):
    monkeypatch.setattr(
        "main.authenticate_user",
        lambda username, password: None,
    )

    response = client.post(
        "/auth/login",
        data={
            "username": "unknown_user",
            "password": "SomePassword123!",
        },
    )

    assert response.status_code == 401


def test_login_disabled_user(monkeypatch):
    # authenticate_user returns None for disabled accounts.
    monkeypatch.setattr(
        "main.authenticate_user",
        lambda username, password: None,
    )

    response = client.post(
        "/auth/login",
        data={
            "username": "disabled_user",
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 401
