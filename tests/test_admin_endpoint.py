from fastapi.testclient import TestClient

from main import app
from token_service import create_access_token


client = TestClient(app)


def make_user(role):
    return {
        "id": 1,
        "username": "demo_user",
        "email": "demo@example.com",
        "role": role,
        "is_active": True,
    }


def test_admin_endpoint_allows_admin(monkeypatch):
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user("admin"),
    )

    token = create_access_token(1)

    response = client.get(
        "/auth/admin-check",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


def test_admin_endpoint_rejects_viewer(monkeypatch):
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user("viewer"),
    )

    token = create_access_token(1)

    response = client.get(
        "/auth/admin-check",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions"


def test_admin_endpoint_requires_authentication():
    response = client.get("/auth/admin-check")

    assert response.status_code == 401
