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
    ["admin", "clinician", "scheduler", "viewer"],
)
def test_authorized_role_can_read_fhir_practitioner(monkeypatch, role):
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user(role),
    )

    monkeypatch.setattr(
        "main.get_provider_by_id",
        lambda provider_id: {
            "id": provider_id,
            "first_name": "Jane",
            "last_name": "Smith",
            "specialty": "Cardiology",
            "email": "jane@example.com",
            "phone": None,
        },
    )

    token = create_access_token(1)

    response = client.get(
        "/fhir/Practitioner/1",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == ("application/fhir+json")
    assert response.json()["resourceType"] == "Practitioner"
    assert response.json()["id"] == "1"
    assert response.json()["name"][0]["family"] == "Smith"


def test_missing_fhir_practitioner_returns_operation_outcome(
    monkeypatch,
):
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user("admin"),
    )
    monkeypatch.setattr(
        "main.get_provider_by_id",
        lambda provider_id: None,
    )

    token = create_access_token(1)

    response = client.get(
        "/fhir/Practitioner/999",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == ("application/fhir+json")
    assert response.json()["resourceType"] == "OperationOutcome"
    assert response.json()["issue"][0]["code"] == "not-found"


def test_fhir_practitioner_requires_authentication():
    response = client.get("/fhir/Practitioner/1")

    assert response.status_code == 401
