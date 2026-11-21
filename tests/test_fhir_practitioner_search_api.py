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
def test_practitioner_search_returns_fhir_bundle(monkeypatch, role):
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user(role),
    )

    def fake_search(family, given, count, offset):
        assert family == "Sm"
        assert given == "Ja"
        assert count == 1
        assert offset == 0

        return [
            {
                "id": 7,
                "first_name": "Jane",
                "last_name": "Smith",
                "specialty": "Cardiology",
                "email": "jane@example.com",
                "phone": None,
            }
        ], 2

    monkeypatch.setattr("routers.fhir.search_providers", fake_search)

    token = create_access_token(1)
    response = client.get(
        "/fhir/Practitioner",
        params={
            "family": "Sm",
            "given": "Ja",
            "_count": 1,
            "_offset": 0,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == ("application/fhir+json")

    bundle = response.json()
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "searchset"
    assert bundle["total"] == 2
    assert len(bundle["entry"]) == 1
    assert bundle["entry"][0]["resource"]["id"] == "7"
    assert bundle["entry"][0]["resource"]["resourceType"] == ("Practitioner")
    assert bundle["entry"][0]["fullUrl"].endswith("/fhir/Practitioner/7")

    links = {link["relation"]: link["url"] for link in bundle["link"]}
    assert "self" in links
    assert "next" in links
    assert "previous" not in links
    assert "_offset=1" in links["next"]
    assert "family=Sm" in links["next"]


def test_practitioner_search_previous_page(monkeypatch):
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user("admin"),
    )
    monkeypatch.setattr(
        "routers.fhir.search_providers",
        lambda **kwargs: ([], 2),
    )

    token = create_access_token(1)
    response = client.get(
        "/fhir/Practitioner",
        params={"_count": 1, "_offset": 1},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    links = {link["relation"]: link["url"] for link in response.json()["link"]}
    assert "previous" in links
    assert "_offset=0" in links["previous"]
    assert "next" not in links


def test_practitioner_search_requires_authentication():
    response = client.get("/fhir/Practitioner")
    assert response.status_code == 401


@pytest.mark.parametrize(
    "params",
    [
        {"_count": 0},
        {"_count": 101},
        {"_offset": -1},
    ],
)
def test_practitioner_search_invalid_pagination(monkeypatch, params):
    monkeypatch.setattr(
        "auth_dependencies.get_user_by_id",
        lambda user_id: make_user("admin"),
    )

    token = create_access_token(1)
    response = client.get(
        "/fhir/Practitioner",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    assert response.json()["resourceType"] == "OperationOutcome"
