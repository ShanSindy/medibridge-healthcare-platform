from fastapi.testclient import TestClient

from main import app
from token_service import create_access_token

client = TestClient(app)


def test_auth_me_requires_token():
    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"] == ("Invalid or missing authentication credentials")
    assert response.headers["www-authenticate"] == "Bearer"


def test_auth_me_rejects_invalid_token():
    response = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401


def test_auth_me_rejects_wrong_auth_scheme():
    response = client.get(
        "/auth/me",
        headers={"Authorization": "Basic invalid-credentials"},
    )

    assert response.status_code == 401


def test_auth_me_accepts_valid_token(monkeypatch):
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

    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == 1
    assert data["username"] == "demo_user"
    assert data["email"] == "demo@example.com"
    assert data["role"] == "viewer"
    assert data["is_active"] is True
    assert "password_hash" not in data
