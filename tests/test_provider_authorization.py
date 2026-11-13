
import pytest
from datetime import datetime, timezone

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
    ["admin", "clinician", "scheduler", "viewer"],
)
def test_authenticated_roles_can_read_providers(
    monkeypatch,
    role,
):
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user(role),
    )

    monkeypatch.setattr(
        "main.get_all_providers",
        lambda: [],
    )

    token = create_access_token(1)

    response = client.get(
        "/providers",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize(
    "role",
    ["clinician", "scheduler", "viewer"],
)
def test_non_admin_roles_cannot_create_providers(
    monkeypatch,
    role,
):
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user(role),
    )

    token = create_access_token(1)

    response = client.post(
        "/providers",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "first_name": "Jane",
            "last_name": "Smith",
            "specialty": "Cardiology",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == (
        "Insufficient permissions"
    )


def test_provider_list_requires_authentication():
    response = client.get("/providers")

    assert response.status_code == 401


def test_provider_creation_requires_authentication():
    response = client.post(
        "/providers",
        json={},
    )

    assert response.status_code == 401


def test_invalid_token_rejected():
    response = client.get(
        "/providers",
        headers={
            "Authorization": "Bearer invalid-token"
        },
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/providers"),
        ("PUT", "/providers/1"),
        ("DELETE", "/providers/1"),
    ],
)
def test_admin_can_manage_providers(
    monkeypatch,
    method,
    path,
):
    # Simulate an authenticated administrator.
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user("admin"),
    )

    # Mock provider creation.
    monkeypatch.setattr(
        "main.create_provider",
        lambda provider: {
            "id": 1,
            **provider.model_dump(),
            "created_at": datetime.now(timezone.utc),
        },
    )

    # Mock provider updates.
    monkeypatch.setattr(
        "main.update_provider",
        lambda provider_id, provider: {
            "id": provider_id,
            **provider.model_dump(),
            "created_at": datetime.now(timezone.utc),
        },
    )

    # Mock provider deletion.
    monkeypatch.setattr(
        "main.delete_provider",
        lambda provider_id: True,
    )

    token = create_access_token(1)

    provider_data = {
        "first_name": "Jane",
        "last_name": "Smith",
        "specialty": "Cardiology",
    }

    response = client.request(
        method,
        path,
        headers={
            "Authorization": f"Bearer {token}"
        },
        json=(
            provider_data
            if method != "DELETE"
            else None
        ),
    )

    expected_status = {
        "POST": 201,
        "PUT": 200,
        "DELETE": 204,
    }

    assert response.status_code == expected_status[method]


@pytest.mark.parametrize(
    "role",
    ["clinician", "scheduler", "viewer"],
)
@pytest.mark.parametrize(
    "method,path",
    [
        ("PUT", "/providers/1"),
        ("DELETE", "/providers/1"),
    ],
)
def test_non_admin_cannot_modify_providers(
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
        headers={
            "Authorization": f"Bearer {token}"
        },
        json={} if method == "PUT" else None,
    )

    assert response.status_code == 403

    assert response.json()["detail"] == (
        "Insufficient permissions"
    )
