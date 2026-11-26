import pytest
from fastapi.testclient import TestClient

import main
from auth_dependencies import get_current_user
from tests.pg_helpers import verified_connection

client = TestClient(main.app)


FHIR_PATIENT_PATHS = [
    "/fhir/Patient",
    "/fhir/Patient/1",
]


def insert_synthetic_patient(disposable_database) -> int:
    """One synthetic patient in this session's database, through a verified connection."""
    with verified_connection(disposable_database) as conn:
        return conn.execute(
            "INSERT INTO patients (first_name, last_name, date_of_birth, gender) "
            "VALUES ('Emily', 'Carter', '1990-05-12', 'female') RETURNING id"
        ).fetchone()[0]


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


def test_clinician_can_read_fhir_patient(disposable_database):
    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 2,
        "username": "test_clinician",
        "email": "clinician@example.com",
        "role": "clinician",
        "is_active": True,
    }
    patient_id = insert_synthetic_patient(disposable_database)

    response = client.get(f"/fhir/Patient/{patient_id}")

    assert response.status_code == 200

    data = response.json()

    assert data["resourceType"] == "Patient"
    assert data["id"] == str(patient_id)


def test_scheduler_can_search_fhir_patients(disposable_database):
    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 3,
        "username": "test_scheduler",
        "email": "scheduler@example.com",
        "role": "scheduler",
        "is_active": True,
    }
    insert_synthetic_patient(disposable_database)

    response = client.get("/fhir/Patient")

    assert response.status_code == 200

    data = response.json()

    assert data["resourceType"] == "Bundle"
    assert data["type"] == "searchset"
    assert data["total"] == 1
    assert len(data["entry"]) == 1
    assert data["entry"][0]["resource"]["resourceType"] == "Patient"
