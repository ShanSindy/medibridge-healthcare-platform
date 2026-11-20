from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from auth_dependencies import get_current_user
from main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def authenticated_admin():
    def fake_current_user():
        return {
            "id": 1,
            "username": "test_admin",
            "email": "admin@example.com",
            "role": "admin",
            "is_active": True,
        }

    app.dependency_overrides[get_current_user] = fake_current_user

    yield

    app.dependency_overrides.pop(get_current_user, None)


def sample_provider():
    return {
        "id": 1,
        "first_name": "Emily",
        "last_name": "Carter",
        "specialty": "Neurology",
        "email": "emily.carter@example.com",
        "phone": "615-555-0199",
        "created_at": datetime(2026, 11, 10, 16, 20, tzinfo=UTC),
    }


def test_get_provider_by_id_success():
    with patch(
        "main.get_provider_by_id",
        return_value=sample_provider(),
    ):
        response = client.get("/providers/1")

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == 1
    assert data["first_name"] == "Emily"
    assert data["last_name"] == "Carter"
    assert data["specialty"] == "Neurology"


def test_get_provider_by_id_not_found():
    with patch(
        "main.get_provider_by_id",
        return_value=None,
    ):
        response = client.get("/providers/99999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Provider not found"}


def test_get_provider_by_id_invalid_id():
    response = client.get("/providers/abc")

    assert response.status_code == 422


def test_create_provider_success():
    provider_data = {
        "first_name": "Emily",
        "last_name": "Carter",
        "specialty": "Neurology",
        "email": "emily.carter@example.com",
        "phone": "615-555-0199",
    }

    with patch(
        "main.create_provider",
        return_value=sample_provider(),
    ) as mock_create:
        response = client.post(
            "/providers",
            json=provider_data,
        )

    assert response.status_code == 201

    data = response.json()

    assert data["id"] == 1
    assert data["first_name"] == "Emily"
    assert data["specialty"] == "Neurology"

    mock_create.assert_called_once()


def test_create_provider_missing_required_field():
    provider_data = {
        "first_name": "Emily",
        "last_name": "Carter",
    }

    with patch("main.create_provider") as mock_create:
        response = client.post(
            "/providers",
            json=provider_data,
        )

    assert response.status_code == 422
    mock_create.assert_not_called()


def test_create_provider_invalid_email():
    provider_data = {
        "first_name": "Emily",
        "last_name": "Carter",
        "specialty": "Neurology",
        "email": "invalid-email",
    }

    with patch("main.create_provider") as mock_create:
        response = client.post(
            "/providers",
            json=provider_data,
        )

    assert response.status_code == 422
    mock_create.assert_not_called()


def test_create_provider_blank_specialty():
    provider_data = {
        "first_name": "Emily",
        "last_name": "Carter",
        "specialty": "   ",
    }

    with patch("main.create_provider") as mock_create:
        response = client.post(
            "/providers",
            json=provider_data,
        )

    assert response.status_code == 422
    mock_create.assert_not_called()


def test_get_all_providers_success():
    providers = [
        sample_provider(),
        {
            "id": 2,
            "first_name": "Michael",
            "last_name": "Reed",
            "specialty": "Dermatology",
            "email": "michael.reed@example.com",
            "phone": "615-555-0123",
            "created_at": datetime(2026, 11, 10, 17, 0, tzinfo=UTC),
        },
    ]

    with patch(
        "main.get_all_providers",
        return_value=providers,
    ) as mock_get_all:
        response = client.get("/providers")

    assert response.status_code == 200

    data = response.json()

    assert len(data) == 2
    assert data[0]["id"] == 1
    assert data[0]["first_name"] == "Emily"
    assert data[1]["id"] == 2
    assert data[1]["first_name"] == "Michael"

    mock_get_all.assert_called_once_with()


def test_get_all_providers_empty():
    with patch(
        "main.get_all_providers",
        return_value=[],
    ) as mock_get_all:
        response = client.get("/providers")

    assert response.status_code == 200
    assert response.json() == []

    mock_get_all.assert_called_once_with()


def test_update_provider_success():
    provider_data = {
        "first_name": "Emily",
        "last_name": "Carter",
        "specialty": "Neurology",
        "email": "emily.carter@example.com",
        "phone": "615-555-0199",
    }

    with patch(
        "main.update_provider",
        return_value=sample_provider(),
    ) as mock_update:
        response = client.put(
            "/providers/1",
            json=provider_data,
        )

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == 1
    assert data["specialty"] == "Neurology"
    assert data["phone"] == "615-555-0199"

    mock_update.assert_called_once()
    assert mock_update.call_args.args[0] == 1


def test_update_provider_not_found():
    provider_data = {
        "first_name": "Emily",
        "last_name": "Carter",
        "specialty": "Neurology",
    }

    with patch(
        "main.update_provider",
        return_value=None,
    ) as mock_update:
        response = client.put(
            "/providers/99999",
            json=provider_data,
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "Provider not found"}

    mock_update.assert_called_once()


def test_update_provider_invalid_data():
    provider_data = {
        "first_name": "Emily",
        "last_name": "Carter",
        "specialty": "   ",
    }

    with patch("main.update_provider") as mock_update:
        response = client.put(
            "/providers/1",
            json=provider_data,
        )

    assert response.status_code == 422
    mock_update.assert_not_called()


def test_delete_provider_success():
    with patch(
        "main.delete_provider",
        return_value=True,
    ) as mock_delete:
        response = client.delete("/providers/1")

    assert response.status_code == 204
    assert response.content == b""

    mock_delete.assert_called_once_with(1)


def test_delete_provider_not_found():
    with patch(
        "main.delete_provider",
        return_value=False,
    ) as mock_delete:
        response = client.delete("/providers/99999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Provider not found"}

    mock_delete.assert_called_once_with(99999)


def test_delete_provider_invalid_id():
    with patch("main.delete_provider") as mock_delete:
        response = client.delete("/providers/abc")

    assert response.status_code == 422
    mock_delete.assert_not_called()
