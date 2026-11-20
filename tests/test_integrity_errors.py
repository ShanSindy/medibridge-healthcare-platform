"""
Regression tests: database integrity violations must become clear API
errors (404 / 409), never HTTP 500.

These run against the real medibridge_test database because the behaviour
comes from PostgreSQL constraints. Every row created here is synthetic and
is removed again in the fixture teardown.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from auth_dependencies import get_current_user
from database import get_connection
from main import app

# raise_server_exceptions=False so an unhandled error shows up as a 500
# response instead of aborting the test.
client = TestClient(app, raise_server_exceptions=False)

MISSING_ID = 2_000_000_000


def _require_test_database(conn) -> None:
    if conn.info.dbname != "medibridge_test":
        raise RuntimeError("Integrity tests must run against medibridge_test.")


@pytest.fixture(autouse=True)
def authenticated_admin():
    original_overrides = app.dependency_overrides.copy()
    # user_id None keeps audit rows free of a foreign key to a test user.
    app.dependency_overrides[get_current_user] = lambda: {
        "id": None,
        "username": "integrity_test_admin",
        "email": "integrity-admin@example.com",
        "role": "admin",
        "is_active": True,
    }
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)


@pytest.fixture
def records():
    """
    Creates synthetic rows on request and deletes everything it created
    afterwards, appointments first because of the foreign keys.
    """
    created = {"appointments": [], "patients": [], "providers": []}

    with get_connection() as conn:
        _require_test_database(conn)

    def add_patient() -> int:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO patients (first_name, last_name, date_of_birth, gender)
                VALUES ('Integrity', 'Synthetic', '1990-01-01', 'unknown')
                RETURNING id
                """
            )
            patient_id = cur.fetchone()[0]
        created["patients"].append(patient_id)
        return patient_id

    def add_provider(email: str | None = None) -> int:
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO providers (first_name, last_name, specialty, email)
                VALUES ('Integrity', 'Synthetic', 'Testing', %s)
                RETURNING id
                """,
                (email,),
            )
            provider_id = cur.fetchone()[0]
        created["providers"].append(provider_id)
        return provider_id

    def add_appointment(patient_id: int, provider_id: int) -> int:
        starts_at = datetime(2031, 1, 6, 9, 0, tzinfo=timezone.utc)
        with get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO appointments (patient_id, provider_id, starts_at, ends_at)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (patient_id, provider_id, starts_at, starts_at + timedelta(minutes=30)),
            )
            appointment_id = cur.fetchone()[0]
        created["appointments"].append(appointment_id)
        return appointment_id

    try:
        yield {
            "patient": add_patient,
            "provider": add_provider,
            "appointment": add_appointment,
            # Register rows created through the API so teardown removes them too.
            "track_appointment": created["appointments"].append,
            "track_provider": created["providers"].append,
        }
    finally:
        with get_connection() as conn, conn.cursor() as cur:
            _require_test_database(conn)
            cur.execute("DELETE FROM appointments WHERE id = ANY(%s)", (created["appointments"],))
            cur.execute("DELETE FROM patients WHERE id = ANY(%s)", (created["patients"],))
            cur.execute("DELETE FROM providers WHERE id = ANY(%s)", (created["providers"],))


def _appointment_body(patient_id: int, provider_id: int) -> dict:
    return {
        "patient_id": patient_id,
        "provider_id": provider_id,
        "starts_at": "2031-02-03T09:00:00Z",
        "ends_at": "2031-02-03T09:30:00Z",
        "reason": "Synthetic integrity test",
    }


def _count(table: str, column: str, value: int) -> int:
    assert table in {"appointments", "patients", "providers"}
    assert column in {"id", "patient_id", "provider_id"}
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table} WHERE {column} = %s", (value,))
        return cur.fetchone()[0]


# --- POST /appointments with references that do not exist -------------------


def test_create_appointment_unknown_patient_returns_404(records):
    provider_id = records["provider"]()

    response = client.post("/appointments", json=_appointment_body(MISSING_ID, provider_id))

    assert response.status_code == 404
    assert response.json() == {"detail": "Patient not found"}
    assert _count("appointments", "provider_id", provider_id) == 0


def test_create_appointment_unknown_provider_returns_404(records):
    patient_id = records["patient"]()

    response = client.post("/appointments", json=_appointment_body(patient_id, MISSING_ID))

    assert response.status_code == 404
    assert response.json() == {"detail": "Provider not found"}
    assert _count("appointments", "patient_id", patient_id) == 0


def test_create_appointment_with_valid_references_still_succeeds(records):
    patient_id = records["patient"]()
    provider_id = records["provider"]()

    response = client.post("/appointments", json=_appointment_body(patient_id, provider_id))

    if response.status_code == 201:
        records["track_appointment"](response.json()["id"])
    assert response.status_code == 201
    assert response.json()["patient_id"] == patient_id
    assert response.json()["provider_id"] == provider_id


# --- DELETE of a patient or provider that still has appointments ------------


def test_delete_patient_with_appointments_returns_409_and_keeps_patient(records):
    patient_id = records["patient"]()
    provider_id = records["provider"]()
    records["appointment"](patient_id, provider_id)

    response = client.delete(f"/patients/{patient_id}")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Patient has appointments and cannot be deleted."
    }
    assert _count("patients", "id", patient_id) == 1
    assert _count("appointments", "patient_id", patient_id) == 1


def test_delete_patient_without_appointments_still_returns_204(records):
    patient_id = records["patient"]()

    response = client.delete(f"/patients/{patient_id}")

    assert response.status_code == 204
    assert _count("patients", "id", patient_id) == 0


def test_delete_provider_with_appointments_returns_409_and_keeps_provider(records):
    patient_id = records["patient"]()
    provider_id = records["provider"]()
    records["appointment"](patient_id, provider_id)

    response = client.delete(f"/providers/{provider_id}")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Provider has appointments and cannot be deleted."
    }
    assert _count("providers", "id", provider_id) == 1


def test_delete_provider_without_appointments_still_returns_204(records):
    provider_id = records["provider"]()

    response = client.delete(f"/providers/{provider_id}")

    assert response.status_code == 204
    assert _count("providers", "id", provider_id) == 0


# --- Duplicate provider email (UNIQUE providers.email) ----------------------


def _provider_body(email: str) -> dict:
    return {
        "first_name": "Integrity",
        "last_name": "Synthetic",
        "specialty": "Testing",
        "email": email,
    }


def test_create_provider_with_duplicate_email_returns_409(records):
    records["provider"]("integrity.duplicate@example.com")

    response = client.post("/providers", json=_provider_body("integrity.duplicate@example.com"))

    if response.status_code == 201:  # keep the database clean if the fix regresses
        records["track_provider"](response.json()["id"])
    assert response.status_code == 409
    assert response.json() == {"detail": "A provider with this email already exists."}


def test_update_provider_to_duplicate_email_returns_409(records):
    records["provider"]("integrity.taken@example.com")
    provider_id = records["provider"]("integrity.other@example.com")

    response = client.put(f"/providers/{provider_id}", json=_provider_body("integrity.taken@example.com"))

    assert response.status_code == 409
    assert response.json() == {"detail": "A provider with this email already exists."}


def test_update_provider_keeping_its_own_email_still_succeeds(records):
    provider_id = records["provider"]("integrity.same@example.com")

    response = client.put(f"/providers/{provider_id}", json=_provider_body("integrity.same@example.com"))

    assert response.status_code == 200
    assert response.json()["email"] == "integrity.same@example.com"
