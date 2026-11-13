
import pytest
from fastapi.testclient import TestClient

import main
from auth_dependencies import get_current_user


client = TestClient(main.app)


FHIR_PATIENT_PATHS = [
    "/fhir/Patient",
    "/fhir/Patient/1",
]


@pytest.fixture(autouse=True)
def reset_auth_overrides():
    # Preserve overrides that may exist before this test.
    original = main.app.dependency_overrides.copy()

    main.app.dependency_overrides.clear()

    yield

    main.app.dependency_overrides.clear()
    main.app.dependency_overrides.update(original)


@pytest.mark.parametrize("path", FHIR_PATIENT_PATHS)
def test_fhir_patient_requires_authentication(path):
    response = client.get(path)

    assert response.status_code == 401


@pytest.mark.parametrize("path", FHIR_PATIENT_PATHS)
def test_viewer_cannot_access_fhir_patients(path):
    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 1,
        "username": "test_viewer",
        "email": "viewer@example.com",
        "role": "viewer",
        "is_active": True,
    }

    response = client.get(path)

    assert response.status_code == 403


def test_fhir_metadata_is_public():
    response = client.get("/fhir/metadata")

    assert response.status_code == 200
    assert response.json()["resourceType"] == "CapabilityStatement"

def test_clinician_can_read_fhir_patient(monkeypatch):
    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 2,
        "username": "test_clinician",
        "email": "clinician@example.com",
        "role": "clinician",
        "is_active": True,
    }

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def execute(self, query, params=None):
            pass

        def fetchone(self):
            return (
                1,
                "Emily",
                "Carter",
                "1990-05-12",
                "female",
            )

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(
        main,
        "get_connection",
        lambda: FakeConnection(),
    )

    monkeypatch.setattr(
        main,
        "get_patient_identifiers",
        lambda patient_id: [],
    )

    response = client.get("/fhir/Patient/1")

    assert response.status_code == 200

    data = response.json()

    assert data["resourceType"] == "Patient"
    assert data["id"] == "1"

def test_scheduler_can_search_fhir_patients(monkeypatch):
    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 3,
        "username": "test_scheduler",
        "email": "scheduler@example.com",
        "role": "scheduler",
        "is_active": True,
    }

    class FakeCursor:
        def __init__(self):
            self.query_count = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def execute(self, query, params=None):
            self.query_count += 1

        def fetchall(self):
            return [
                (1, "Emily", "Carter", "1990-05-12", "female")
            ]

        def fetchone(self):
            return (1,)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(
        main,
        "get_connection",
        lambda: FakeConnection(),
    )

    monkeypatch.setattr(
        main,
        "get_identifiers_for_patients",
        lambda patient_ids: {},
    )

    response = client.get("/fhir/Patient")

    assert response.status_code == 200

    data = response.json()

    assert data["resourceType"] == "Bundle"
    assert data["type"] == "searchset"
    assert data["total"] == 1
    assert len(data["entry"]) == 1
    assert data["entry"][0]["resource"]["resourceType"] == "Patient"
