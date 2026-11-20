from fastapi.testclient import TestClient

import main, pytest
from auth_dependencies import get_current_user


client = TestClient(main.app)


def test_patient_creation_records_audit_event(monkeypatch):
    original_overrides = main.app.dependency_overrides.copy()

    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 42,
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
                123,
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

    fake_connection = FakeConnection()
    captured_audit = {}

    def fake_log_audit_event(**kwargs):
        captured_audit.update(kwargs)

    monkeypatch.setattr(
        main,
        "get_connection",
        lambda: fake_connection,
    )

    monkeypatch.setattr(
        main,
        "log_audit_event",
        fake_log_audit_event,
    )

    try:
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

        assert response.json()["id"] == 123

        assert captured_audit["action"] == "patient.create"
        assert captured_audit["resource_type"] == "patient"
        assert captured_audit["resource_id"] == "123"
        assert captured_audit["outcome"] == "success"
        assert captured_audit["user_id"] == 42
        assert captured_audit["details"] == {"source": "api"}
        assert captured_audit["connection"] is fake_connection

    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(original_overrides)


def test_patient_creation_rolls_back_when_audit_fails(monkeypatch):
    original_overrides = main.app.dependency_overrides.copy()

    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 42,
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
                123,
                "Emily",
                "Carter",
                "1990-05-12",
                "female",
            )

    class FakeConnection:
        def __init__(self):
            self.rolled_back = False
            self.committed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            if exc_type is not None:
                self.rolled_back = True
            else:
                self.committed = True

            return False

        def cursor(self):
            return FakeCursor()

    fake_connection = FakeConnection()

    def failing_audit_logger(**kwargs):
        raise RuntimeError("Simulated audit database failure")

    monkeypatch.setattr(
        main,
        "get_connection",
        lambda: fake_connection,
    )

    monkeypatch.setattr(
        main,
        "log_audit_event",
        failing_audit_logger,
    )

    try:
        with pytest.raises(
            RuntimeError,
            match="Simulated audit database failure",
        ):
            client.post(
                "/patients",
                json={
                    "first_name": "Emily",
                    "last_name": "Carter",
                    "date_of_birth": "1990-05-12",
                    "gender": "female",
                },
            )

        assert fake_connection.rolled_back is True
        assert fake_connection.committed is False

    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(original_overrides)


def test_patient_update_records_audit_event(monkeypatch):
    original_overrides = main.app.dependency_overrides.copy()

    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 42,
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
                123,
                "Emily",
                "Johnson",
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

    fake_connection = FakeConnection()
    captured_audit = {}

    def fake_log_audit_event(**kwargs):
        captured_audit.update(kwargs)

    monkeypatch.setattr(
        main,
        "get_connection",
        lambda: fake_connection,
    )

    monkeypatch.setattr(
        main,
        "log_audit_event",
        fake_log_audit_event,
    )

    try:
        response = client.put(
            "/patients/123",
            json={
                "first_name": "Emily",
                "last_name": "Johnson",
                "date_of_birth": "1990-05-12",
                "gender": "female",
            },
        )

        assert response.status_code == 200
        assert response.json()["id"] == 123
        assert response.json()["last_name"] == "Johnson"

        assert captured_audit["action"] == "patient.update"
        assert captured_audit["resource_type"] == "patient"
        assert captured_audit["resource_id"] == "123"
        assert captured_audit["outcome"] == "success"
        assert captured_audit["user_id"] == 42
        assert captured_audit["connection"] is fake_connection

    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(original_overrides)


def test_patient_update_rolls_back_when_audit_fails(monkeypatch):
    original_overrides = main.app.dependency_overrides.copy()

    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 42,
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
                123,
                "Emily",
                "Johnson",
                "1990-05-12",
                "female",
            )

    class FakeConnection:
        def __init__(self):
            self.rolled_back = False
            self.committed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            if exc_type is not None:
                self.rolled_back = True
            else:
                self.committed = True

            return False

        def cursor(self):
            return FakeCursor()

    fake_connection = FakeConnection()

    def failing_audit_logger(**kwargs):
        raise RuntimeError("Simulated audit database failure")

    monkeypatch.setattr(
        main,
        "get_connection",
        lambda: fake_connection,
    )

    monkeypatch.setattr(
        main,
        "log_audit_event",
        failing_audit_logger,
    )

    try:
        with pytest.raises(
            RuntimeError,
            match="Simulated audit database failure",
        ):
            client.put(
                "/patients/123",
                json={
                    "first_name": "Emily",
                    "last_name": "Johnson",
                    "date_of_birth": "1990-05-12",
                    "gender": "female",
                },
            )

        assert fake_connection.rolled_back is True
        assert fake_connection.committed is False

    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(original_overrides)


def test_patient_deletion_records_audit_event(monkeypatch):
    original_overrides = main.app.dependency_overrides.copy()

    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 42,
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
            return (123,)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def cursor(self):
            return FakeCursor()

    fake_connection = FakeConnection()
    captured_audit = {}

    def fake_log_audit_event(**kwargs):
        captured_audit.update(kwargs)

    monkeypatch.setattr(
        main,
        "get_connection",
        lambda: fake_connection,
    )

    monkeypatch.setattr(
        main,
        "log_audit_event",
        fake_log_audit_event,
    )

    try:
        response = client.delete("/patients/123")

        assert response.status_code == 204
        assert response.content == b""

        assert captured_audit["action"] == "patient.delete"
        assert captured_audit["resource_type"] == "patient"
        assert captured_audit["resource_id"] == "123"
        assert captured_audit["outcome"] == "success"
        assert captured_audit["user_id"] == 42
        assert captured_audit["connection"] is fake_connection

    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(original_overrides)


def test_patient_deletion_rolls_back_when_audit_fails(monkeypatch):
    original_overrides = main.app.dependency_overrides.copy()

    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": 42,
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
            return (123,)

    class FakeConnection:
        def __init__(self):
            self.rolled_back = False
            self.committed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            if exc_type is not None:
                self.rolled_back = True
            else:
                self.committed = True

            return False

        def cursor(self):
            return FakeCursor()

    fake_connection = FakeConnection()

    def failing_audit_logger(**kwargs):
        raise RuntimeError("Simulated audit database failure")

    monkeypatch.setattr(
        main,
        "get_connection",
        lambda: fake_connection,
    )

    monkeypatch.setattr(
        main,
        "log_audit_event",
        failing_audit_logger,
    )

    try:
        with pytest.raises(
            RuntimeError,
            match="Simulated audit database failure",
        ):
            client.delete("/patients/123")

        assert fake_connection.rolled_back is True
        assert fake_connection.committed is False

    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(original_overrides)
