import pytest
from fastapi.testclient import TestClient

from main import app
from token_service import create_access_token


client = TestClient(app)


def make_user(role="admin"):
    return {
        "id": 1,
        "username": "demo_user",
        "email": "demo@example.com",
        "role": role,
        "is_active": True,
    }


def provider(provider_id=7):
    return {
        "id": provider_id,
        "first_name": "Jane",
        "last_name": "Smith",
        "specialty": "Cardiology",
        "email": None,
        "phone": None,
    }


def authorize(monkeypatch, role="admin"):
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user(role),
    )
    return {"Authorization": f"Bearer {create_access_token(1)}"}


@pytest.mark.parametrize(
    "role",
    ["admin", "clinician", "scheduler", "viewer"],
)
def test_authorized_roles_can_search_practitioner_roles(
    monkeypatch, role
):
    headers = authorize(monkeypatch, role)
    captured = {}

    def fake_search(**kwargs):
        captured.update(kwargs)
        return [provider()], 1

    monkeypatch.setattr("main.search_providers", fake_search)

    response = client.get(
        "/fhir/PractitionerRole",
        params={"specialty": "Card"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/fhir+json"
    assert captured["specialty"] == "Card"

    bundle = response.json()
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "searchset"
    assert bundle["total"] == 1
    assert len(bundle["entry"]) == 1

    entry = bundle["entry"][0]
    assert entry["fullUrl"].endswith("/fhir/PractitionerRole/7")
    assert entry["resource"]["resourceType"] == "PractitionerRole"
    assert entry["resource"]["specialty"] == [{"text": "Cardiology"}]


def test_practitioner_role_search_pagination(monkeypatch):
    headers = authorize(monkeypatch)
    captured = {}

    def fake_search(**kwargs):
        captured.update(kwargs)
        return [provider(8)], 12

    monkeypatch.setattr("main.search_providers", fake_search)

    response = client.get(
        "/fhir/PractitionerRole",
        params={"specialty": "Card", "_count": 2, "_offset": 4},
        headers=headers,
    )

    assert response.status_code == 200
    assert captured == {
        "specialty": "Card",
        "count": 2,
        "offset": 4,
    }

    links = {
        link["relation"]: link["url"]
        for link in response.json()["link"]
    }

    assert set(links) == {"self", "next", "previous"}
    assert "specialty=Card" in links["self"]
    assert "_offset=4" in links["self"]
    assert "_offset=6" in links["next"]
    assert "_offset=2" in links["previous"]


def test_practitioner_role_search_empty_results(monkeypatch):
    headers = authorize(monkeypatch)

    monkeypatch.setattr(
        "main.search_providers",
        lambda **kwargs: ([], 0),
    )

    response = client.get(
        "/fhir/PractitionerRole",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert response.json()["entry"] == []


def test_practitioner_role_search_requires_authentication():
    response = client.get("/fhir/PractitionerRole")

    assert response.status_code == 401


@pytest.mark.parametrize(
    "params",
    [
        {"_count": 0},
        {"_count": 101},
        {"_offset": -1},
    ],
)
def test_practitioner_role_search_rejects_invalid_pagination(
    monkeypatch, params
):
    headers = authorize(monkeypatch)

    response = client.get(
        "/fhir/PractitionerRole",
        params=params,
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["resourceType"] == "OperationOutcome"
