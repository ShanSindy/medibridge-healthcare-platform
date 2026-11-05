
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/health")

    assert response.status_code == 200

    assert response.json() == {
        "status": "healthy",
        "service": "MediBridge API"
    }

def test_home():
    response = client.get("/")

    assert response.status_code == 200

    assert response.json() == {
        "application": "MediBridge",
        "status": "running"
    }

def test_get_patients():
    response = client.get("/patients")

    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_create_patient():
    patient_data = {
        "first_name": "Test",
        "last_name": "Patient",
        "date_of_birth": "2000-01-01",
        "gender": "other"
    }

    patient_id = None

    try:
        response = client.post("/patients", json=patient_data)

        assert response.status_code == 201

        created_patient = response.json()
        patient_id = created_patient["id"]

        assert created_patient["first_name"] == "Test"
        assert created_patient["last_name"] == "Patient"

    finally:
        if patient_id is not None:
            client.delete(f"/patients/{patient_id}")

def test_get_patient_not_found():
    response = client.get("/patients/999999")

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Patient not found"
    }

def test_update_patient():
    patient_data = {
        "first_name": "Original",
        "last_name": "Patient",
        "date_of_birth": "2000-01-01",
        "gender": "other"
    }

    create_response = client.post("/patients", json=patient_data)
    assert create_response.status_code == 201

    patient_id = create_response.json()["id"]

    try:
        updated_data = {
            "first_name": "Updated",
            "last_name": "Patient",
            "date_of_birth": "2000-01-01",
            "gender": "other"
        }

        response = client.put(
            f"/patients/{patient_id}",
            json=updated_data
        )

        assert response.status_code == 200
        assert response.json()["first_name"] == "Updated"
        assert response.json()["id"] == patient_id

    finally:
        client.delete(f"/patients/{patient_id}")

def test_delete_patient():
    patient_data = {
        "first_name": "Delete",
        "last_name": "Test",
        "date_of_birth": "2000-01-01",
        "gender": "other"
    }

    create_response = client.post("/patients", json=patient_data)
    assert create_response.status_code == 201

    patient_id = create_response.json()["id"]

    try:
        delete_response = client.delete(f"/patients/{patient_id}")

        assert delete_response.status_code == 204

        get_response = client.get(f"/patients/{patient_id}")

        assert get_response.status_code == 404

    finally:
        client.delete(f"/patients/{patient_id}")

def test_database_isolation():
    from database import get_connection

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            database_name = cursor.fetchone()[0]

    assert database_name == "medibridge_test"


def test_create_patient_empty_first_name():
    patient_data = {
        "first_name": "",
        "last_name": "Patient",
        "date_of_birth": "2000-01-01",
        "gender": "other"
    }

    response = client.post("/patients", json=patient_data)

    if response.status_code == 201:
        patient_id = response.json()["id"]
        client.delete(f"/patients/{patient_id}")

    assert response.status_code == 422


def test_create_patient_whitespace_first_name():
    patient_data = {
        "first_name": "   ",
        "last_name": "Patient",
        "date_of_birth": "2000-01-01",
        "gender": "other"
    }

    response = client.post("/patients", json=patient_data)

    if response.status_code == 201:
        patient_id = response.json()["id"]
        client.delete(f"/patients/{patient_id}")

    assert response.status_code == 422

def test_create_patient_future_date_of_birth():
    patient_data = {
        "first_name": "Future",
        "last_name": "Patient",
        "date_of_birth": "2099-01-01",
        "gender": "other"
    }

    response = client.post("/patients", json=patient_data)

    if response.status_code == 201:
        patient_id = response.json()["id"]
        client.delete(f"/patients/{patient_id}")

    assert response.status_code == 422

def test_create_patient_invalid_gender():
    patient_data = {
        "first_name": "Test",
        "last_name": "Patient",
        "date_of_birth": "2000-01-01",
        "gender": "invalid_value"
    }

    response = client.post("/patients", json=patient_data)

    if response.status_code == 201:
        patient_id = response.json()["id"]
        client.delete(f"/patients/{patient_id}")

    assert response.status_code == 422
