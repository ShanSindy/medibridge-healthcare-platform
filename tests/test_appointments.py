
from fastapi.testclient import TestClient
from psycopg.errors import ExclusionViolation
import main


client = TestClient(main.app)


import pytest
from auth_dependencies import get_current_user


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

    main.app.dependency_overrides[get_current_user] = fake_current_user

    yield

    main.app.dependency_overrides.pop(get_current_user, None)


def test_list_appointments_returns_appointments(monkeypatch):
    sample_appointments = [
        {
            "id": 1,
            "patient_id": 13,
            "provider_id": 1,
            "starts_at": "2026-11-20T10:00:00-06:00",
            "ends_at": "2026-11-20T10:30:00-06:00",
            "status": "scheduled",
            "reason": "Neurology consultation",
            "created_at": "2026-11-11T16:10:45-06:00",
        }
    ]

    def fake_get_all_appointments(
        patient_id=None,
        provider_id=None,
        status=None,
    ):
        return sample_appointments

    monkeypatch.setattr(
        main,
        "get_all_appointments",
        fake_get_all_appointments,
    )

    response = client.get("/appointments")

    assert response.status_code == 200

    data = response.json()

    assert len(data) == 1
    assert data[0]["id"] == 1
    assert data[0]["patient_id"] == 13
    assert data[0]["provider_id"] == 1
    assert data[0]["status"] == "scheduled"

def test_create_appointment_returns_201(monkeypatch):
    def fake_create_appointment(appointment):
        return {
            "id": 2,
            "patient_id": appointment.patient_id,
            "provider_id": appointment.provider_id,
            "starts_at": appointment.starts_at,
            "ends_at": appointment.ends_at,
            "status": "scheduled",
            "reason": appointment.reason,
            "created_at": "2026-11-11T16:10:45-06:00",
        }

    monkeypatch.setattr(
        main,
        "create_appointment",
        fake_create_appointment,
    )

    payload = {
        "patient_id": 13,
        "provider_id": 1,
        "starts_at": "2026-11-20T10:00:00-06:00",
        "ends_at": "2026-11-20T10:30:00-06:00",
        "reason": "Neurology consultation",
    }

    response = client.post(
        "/appointments",
        json=payload,
    )

    assert response.status_code == 201

    data = response.json()

    assert data["id"] == 2
    assert data["patient_id"] == 13
    assert data["provider_id"] == 1
    assert data["status"] == "scheduled"
    assert data["reason"] == "Neurology consultation"


def test_create_appointment_conflict_returns_409(monkeypatch):
    class FakeExclusionViolation(ExclusionViolation):
        @property
        def diag(self):
            class FakeDiagnostic:
                constraint_name = "appointments_no_provider_overlap"

            return FakeDiagnostic()

    def fake_create_appointment(appointment):
        raise FakeExclusionViolation(
            "conflicting key value violates exclusion constraint"
        )

    monkeypatch.setattr(
        main,
        "create_appointment",
        fake_create_appointment,
    )

    payload = {
        "patient_id": 13,
        "provider_id": 1,
        "starts_at": "2026-11-20T10:15:00-06:00",
        "ends_at": "2026-11-20T10:45:00-06:00",
        "reason": "Overlapping appointment test",
    }

    response = client.post(
        "/appointments",
        json=payload,
    )

    assert response.status_code == 409

    assert response.json() == {
        "detail": (
            "This provider already has a scheduled "
            "appointment during the requested time."
        )
    }

def test_update_appointment_conflict_returns_409(monkeypatch):
    class FakeExclusionViolation(ExclusionViolation):
        @property
        def diag(self):
            class FakeDiagnostic:
                constraint_name = "appointments_no_provider_overlap"

            return FakeDiagnostic()

    def fake_update_appointment(appointment_id, appointment):
        raise FakeExclusionViolation(
            "conflicting key value violates exclusion constraint"
        )

    monkeypatch.setattr(
        main,
        "update_appointment",
        fake_update_appointment,
    )

    payload = {
        "starts_at": "2026-11-20T10:15:00-06:00",
        "ends_at": "2026-11-20T10:45:00-06:00",
        "status": "scheduled",
        "reason": "Rescheduling conflict test",
    }

    response = client.put(
        "/appointments/1",
        json=payload,
    )

    assert response.status_code == 409

    assert response.json() == {
        "detail": (
            "This provider already has a scheduled "
            "appointment during the requested time."
        )
    }

def test_cancel_appointment_returns_cancelled_status(monkeypatch):
    def fake_cancel_appointment(appointment_id):
        assert appointment_id == 1

        return {
            "id": 1,
            "patient_id": 13,
            "provider_id": 1,
            "starts_at": "2026-11-20T10:00:00-06:00",
            "ends_at": "2026-11-20T10:30:00-06:00",
            "status": "cancelled",
            "reason": "Neurology consultation",
            "created_at": "2026-11-11T16:10:45-06:00",
        }

    monkeypatch.setattr(
        main,
        "cancel_appointment",
        fake_cancel_appointment,
    )

    response = client.patch("/appointments/1/cancel")

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == 1
    assert data["patient_id"] == 13
    assert data["provider_id"] == 1
    assert data["status"] == "cancelled"

def test_create_appointment_rejects_invalid_time(monkeypatch):
    def fake_create_appointment(appointment):
        raise AssertionError(
            "Database service should not be called for invalid input"
        )

    monkeypatch.setattr(
        main,
        "create_appointment",
        fake_create_appointment,
    )

    payload = {
        "patient_id": 13,
        "provider_id": 1,
        "starts_at": "2026-11-20T10:30:00-06:00",
        "ends_at": "2026-11-20T10:00:00-06:00",
        "reason": "Invalid appointment time test",
    }

    response = client.post(
        "/appointments",
        json=payload,
    )

    assert response.status_code == 422

    data = response.json()

    assert "detail" in data
    assert any(
        "Appointment end time must be after start time"
        in error["msg"]
        for error in data["detail"]
    )

def test_get_appointment_not_found_returns_404(monkeypatch):
    def fake_get_appointment_by_id(appointment_id):
        assert appointment_id == 999999
        return None

    monkeypatch.setattr(
        main,
        "get_appointment_by_id",
        fake_get_appointment_by_id,
    )

    response = client.get("/appointments/999999")

    assert response.status_code == 404

    assert response.json() == {
        "detail": "Appointment not found"
    }
