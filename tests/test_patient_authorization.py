import pytest
from fastapi.testclient import TestClient

import main
from auth_dependencies import get_current_user
from routers import patients as patients_router
from tests.pg_helpers import verified_connection

client = TestClient(main.app)


PATIENT_ROUTES = [
    ("GET", "/patients", "read"),
    ("GET", "/patients/1", "read"),
    ("POST", "/patients", "write"),
    ("PUT", "/patients/1", "write"),
    ("DELETE", "/patients/1", "write"),
]


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    yield
    main.app.dependency_overrides.clear()


@pytest.mark.parametrize("method,path,permission", PATIENT_ROUTES)
def test_patient_routes_require_authentication(method, path, permission):
    response = client.request(method, path)

    assert response.status_code == 401


@pytest.mark.parametrize("method,path,permission", PATIENT_ROUTES)
def test_viewer_cannot_access_patient_routes(method, path, permission):
    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 1,
        "username": "test_viewer",
        "email": "viewer@example.com",
        "role": "viewer",
        "is_active": True,
    }

    response = client.request(method, path)

    assert response.status_code == 403


@pytest.mark.parametrize("method,path,permission", PATIENT_ROUTES)
def test_scheduler_cannot_modify_patients(method, path, permission):
    if permission != "write":
        pytest.skip("Scheduler has patient read permission")

    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 2,
        "username": "test_scheduler",
        "email": "scheduler@example.com",
        "role": "scheduler",
        "is_active": True,
    }

    response = client.request(method, path)

    assert response.status_code == 403


def test_scheduler_can_read_patients(disposable_database):
    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 2,
        "username": "test_scheduler",
        "email": "scheduler@example.com",
        "role": "scheduler",
        "is_active": True,
    }
    with verified_connection(disposable_database) as conn:
        conn.execute(
            "INSERT INTO patients (first_name, last_name, date_of_birth, gender) "
            "VALUES ('Emily', 'Carter', '1990-05-12', 'female')"
        )

    response = client.get("/patients")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["first_name"] == "Emily"


@pytest.mark.parametrize("role", ["admin", "clinician"])
def test_authorized_roles_can_create_patients(monkeypatch, role):
    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 1,
        "username": f"test_{role}",
        "email": f"{role}@example.com",
        "role": role,
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
        patients_router,
        "get_connection",
        lambda: FakeConnection(),
    )

    response = client.post(
        "/patients",
        json={
            "first_name": "Emily",
            "last_name": "Carter",
            "date_of_birth": "1990-05-12",
            "gender": "female",
        },
    )

    assert response.status_code == 201
    assert response.json()["first_name"] == "Emily"
    assert response.json()["last_name"] == "Carter"
