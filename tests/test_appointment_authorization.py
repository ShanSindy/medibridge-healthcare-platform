import pytest
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


@pytest.mark.parametrize(
    "role",
    ["admin", "clinician", "scheduler"],
)
def test_allowed_roles_can_read_appointments(
    monkeypatch,
    role,
):
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user(role),
    )

    monkeypatch.setattr(
        "routers.appointments.get_all_appointments",
        lambda **kwargs: [],
    )

    token = create_access_token(1)

    response = client.get(
        "/appointments",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_viewer_cannot_read_appointments(monkeypatch):
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user("viewer"),
    )

    token = create_access_token(1)

    response = client.get(
        "/appointments",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == ("Insufficient permissions")


@pytest.mark.parametrize(
    "role",
    ["clinician", "viewer"],
)
@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/appointments"),
        ("PUT", "/appointments/1"),
        ("PATCH", "/appointments/1/cancel"),
    ],
)
def test_unauthorized_roles_cannot_modify_appointments(
    monkeypatch,
    role,
    method,
    path,
):
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user(role),
    )

    token = create_access_token(1)

    response = client.request(
        method,
        path,
        headers={"Authorization": f"Bearer {token}"},
        json={} if method != "PATCH" else None,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == ("Insufficient permissions")


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/appointments"),
        ("GET", "/appointments/1"),
        ("POST", "/appointments"),
        ("PUT", "/appointments/1"),
        ("PATCH", "/appointments/1/cancel"),
    ],
)
def test_appointment_endpoints_require_authentication(
    method,
    path,
):
    response = client.request(
        method,
        path,
        json={} if method in ("POST", "PUT") else None,
    )

    assert response.status_code == 401


def test_invalid_token_cannot_read_appointments():
    response = client.get(
        "/appointments",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401
