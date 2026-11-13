
import json

import pytest

import audit


class FakeCursor:
    def __init__(self):
        self.query = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params=None):
        self.query = query
        self.params = params


class FakeConnection:
    def __init__(self):
        self.fake_cursor = FakeCursor()
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.committed = True


def test_log_audit_event_success(monkeypatch):
    fake_connection = FakeConnection()

    monkeypatch.setattr(
        audit,
        "get_connection",
        lambda: fake_connection,
    )

    audit.log_audit_event(
        action="patient.create",
        resource_type="patient",
        resource_id="123",
        outcome="success",
        user_id=1,
        details={"source": "api"},
    )

    assert fake_connection.committed is True

    query = fake_connection.fake_cursor.query
    params = fake_connection.fake_cursor.params

    assert "INSERT INTO audit_logs" in query
    assert params[0] == 1
    assert params[1] == "patient.create"
    assert params[2] == "patient"
    assert params[3] == "123"
    assert params[4] == "success"
    assert json.loads(params[5]) == {"source": "api"}


def test_log_audit_event_without_optional_fields(monkeypatch):
    fake_connection = FakeConnection()

    monkeypatch.setattr(
        audit,
        "get_connection",
        lambda: fake_connection,
    )

    audit.log_audit_event(
        action="system.check",
        resource_type="system",
        outcome="success",
    )

    params = fake_connection.fake_cursor.params

    assert params[0] is None
    assert params[3] is None
    assert params[5] is None
    assert fake_connection.committed is True


@pytest.mark.parametrize(
    "invalid_outcome",
    ["pending", "unknown", "", "SUCCESS"],
)
def test_log_audit_event_rejects_invalid_outcome(
    monkeypatch,
    invalid_outcome,
):
    def unexpected_connection():
        raise AssertionError(
            "Database should not be accessed"
        )

    monkeypatch.setattr(
        audit,
        "get_connection",
        unexpected_connection,
    )

    with pytest.raises(ValueError):
        audit.log_audit_event(
            action="patient.create",
            resource_type="patient",
            outcome=invalid_outcome,
        )

def test_log_audit_event_uses_existing_transaction(monkeypatch):
    fake_connection = FakeConnection()

    def unexpected_connection():
        raise AssertionError(
            "Audit logger should reuse the provided connection"
        )

    monkeypatch.setattr(
        audit,
        "get_connection",
        unexpected_connection,
    )

    audit.log_audit_event(
        action="patient.create",
        resource_type="patient",
        resource_id="123",
        outcome="success",
        user_id=42,
        details={"source": "api"},
        connection=fake_connection,
    )

    assert fake_connection.committed is False

    query = fake_connection.fake_cursor.query
    params = fake_connection.fake_cursor.params

    assert "INSERT INTO audit_logs" in query
    assert params[0] == 42
    assert params[1] == "patient.create"
    assert params[2] == "patient"
    assert params[3] == "123"
    assert params[4] == "success"
    assert json.loads(params[5]) == {"source": "api"}
