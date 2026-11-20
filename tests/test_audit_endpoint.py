import pytest
from fastapi.testclient import TestClient

from auth_dependencies import get_current_user
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def make_user(role):
    return {
        "id": 1,
        "username": "testuser",
        "email": "test@example.com",
        "role": role,
        "is_active": True,
    }


def test_admin_can_read_audit_logs(monkeypatch):
    app.dependency_overrides[get_current_user] = lambda: make_user("admin")

    monkeypatch.setattr(
        "main.get_audit_logs",
        lambda limit, offset: [],
    )

    response = client.get("/audit-logs")

    assert response.status_code == 200
    assert response.json() == {
        "limit": 50,
        "offset": 0,
        "items": [],
    }


@pytest.mark.parametrize(
    "role",
    ["clinician", "scheduler", "viewer"],
)
def test_non_admin_cannot_read_audit_logs(role):
    app.dependency_overrides[get_current_user] = lambda: make_user(role)

    response = client.get("/audit-logs")

    assert response.status_code == 403


def test_unauthenticated_user_cannot_read_audit_logs():
    response = client.get("/audit-logs")

    assert response.status_code == 401


def test_audit_endpoint_custom_pagination(monkeypatch):
    app.dependency_overrides[get_current_user] = lambda: make_user("admin")

    captured = {}

    def mock_get_audit_logs(limit, offset):
        captured["limit"] = limit
        captured["offset"] = offset
        return []

    monkeypatch.setattr(
        "main.get_audit_logs",
        mock_get_audit_logs,
    )

    response = client.get("/audit-logs?limit=20&offset=10")

    assert response.status_code == 200
    assert response.json() == {
        "limit": 20,
        "offset": 10,
        "items": [],
    }
    assert captured == {
        "limit": 20,
        "offset": 10,
    }


@pytest.mark.parametrize(
    "query_string",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
    ],
)
def test_audit_endpoint_rejects_invalid_pagination(
    query_string,
    monkeypatch,
):
    app.dependency_overrides[get_current_user] = lambda: make_user("admin")

    def unexpected_database_call(limit, offset):
        pytest.fail("Database should not be queried for invalid pagination")

    monkeypatch.setattr(
        "main.get_audit_logs",
        unexpected_database_call,
    )

    response = client.get(f"/audit-logs?{query_string}")

    assert response.status_code == 422


def test_audit_endpoint_returns_metadata_only(monkeypatch):
    app.dependency_overrides[get_current_user] = lambda: make_user("admin")

    monkeypatch.setattr(
        "main.get_audit_logs",
        lambda limit, offset: [
            {
                "id": 1,
                "user_id": 5,
                "action": "patient.create",
                "resource_type": "patient",
                "resource_id": "42",
                "outcome": "success",
                "created_at": "2026-11-12T12:00:00Z",
            }
        ],
    )

    response = client.get("/audit-logs")

    assert response.status_code == 200

    item = response.json()["items"][0]

    assert item["action"] == "patient.create"
    assert item["resource_id"] == "42"
    assert "details" not in item
