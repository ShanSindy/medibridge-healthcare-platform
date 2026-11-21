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
def test_authorized_role_can_read_fhir_practitioner_role(monkeypatch, role):
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user(role),
    )

    monkeypatch.setattr(
        "routers.fhir.get_provider_by_id",
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
        "/fhir/PractitionerRole/1",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == ("application/fhir+json")

    resource = response.json()
    assert resource["resourceType"] == "PractitionerRole"
    assert resource["id"] == "1"
    assert resource["practitioner"]["reference"] == "Practitioner/1"
    assert resource["specialty"] == [{"text": "Cardiology"}]


def test_missing_fhir_practitioner_role_returns_operation_outcome(
    monkeypatch,
):
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user("admin"),
    )
    monkeypatch.setattr(
        "routers.fhir.get_provider_by_id",
        lambda provider_id: None,
    )

    token = create_access_token(1)

    response = client.get(
        "/fhir/PractitionerRole/999",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == ("application/fhir+json")
    assert response.json()["resourceType"] == "OperationOutcome"
    assert response.json()["issue"][0]["code"] == "not-found"


def test_fhir_practitioner_role_requires_authentication():
    response = client.get("/fhir/PractitionerRole/1")

    assert response.status_code == 401
