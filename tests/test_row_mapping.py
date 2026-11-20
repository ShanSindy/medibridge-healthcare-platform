"""
Row-to-dict mapping in the service layer uses zip(..., strict=True).

Each appointment query returns exactly the eight columns listed next to it,
and the audit query maps rows against the cursor's own column descriptions.
If a query and its column list ever drift apart, the mapping must fail loudly
instead of silently dropping or mislabelling fields.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import appointment_service
import audit_service
from models import AppointmentCreate, AppointmentUpdate

APPOINTMENT_COLUMNS = [
    "id",
    "patient_id",
    "provider_id",
    "starts_at",
    "ends_at",
    "status",
    "reason",
    "created_at",
]
STARTS = datetime(2031, 3, 3, 9, 0, tzinfo=UTC)
ENDS = datetime(2031, 3, 3, 9, 30, tzinfo=UTC)
FULL_ROW = (7, 1, 2, STARTS, ENDS, "scheduled", "Synthetic visit", STARTS)


class FakeCursor:
    def __init__(self, rows, description=None):
        self.rows = rows
        self.description = description

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        pass

    def fetchone(self):
        return self.rows[0]

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor


def _use_rows(monkeypatch, module, rows, description=None):
    monkeypatch.setattr(
        module, "get_connection", lambda: FakeConnection(FakeCursor(rows, description))
    )


APPOINTMENT_CALLS = {
    "create": lambda: appointment_service.create_appointment(
        AppointmentCreate(patient_id=1, provider_id=2, starts_at=STARTS, ends_at=ENDS)
    ),
    "list": lambda: appointment_service.get_all_appointments()[0],
    "get": lambda: appointment_service.get_appointment_by_id(7),
    "update": lambda: appointment_service.update_appointment(
        7, AppointmentUpdate(starts_at=STARTS, ends_at=ENDS, status="scheduled")
    ),
    "cancel": lambda: appointment_service.cancel_appointment(7),
}


@pytest.mark.parametrize("operation", APPOINTMENT_CALLS)
def test_appointment_rows_map_every_column(monkeypatch, operation):
    _use_rows(monkeypatch, appointment_service, [FULL_ROW])

    result = APPOINTMENT_CALLS[operation]()

    assert list(result) == APPOINTMENT_COLUMNS
    assert result["id"] == 7
    assert result["created_at"] == STARTS


@pytest.mark.parametrize("operation", APPOINTMENT_CALLS)
@pytest.mark.parametrize("row", [FULL_ROW[:-1], (*FULL_ROW, "unexpected")], ids=["short", "long"])
def test_appointment_rows_that_do_not_match_the_columns_fail(monkeypatch, operation, row):
    _use_rows(monkeypatch, appointment_service, [row])

    with pytest.raises(ValueError, match="zip"):
        APPOINTMENT_CALLS[operation]()


AUDIT_COLUMNS = ["id", "user_id", "action", "resource_type", "resource_id", "outcome", "created_at"]
AUDIT_ROW = (1, None, "patient.create", "patient", "7", "success", STARTS)


def _description(names):
    return [SimpleNamespace(name=name) for name in names]


def test_audit_rows_map_every_column(monkeypatch):
    _use_rows(monkeypatch, audit_service, [AUDIT_ROW], _description(AUDIT_COLUMNS))

    [event] = audit_service.get_audit_logs(limit=10, offset=0)

    assert list(event) == AUDIT_COLUMNS
    assert event["action"] == "patient.create"


def test_audit_rows_that_do_not_match_the_description_fail(monkeypatch):
    _use_rows(monkeypatch, audit_service, [AUDIT_ROW[:-1]], _description(AUDIT_COLUMNS))

    with pytest.raises(ValueError, match="zip"):
        audit_service.get_audit_logs(limit=10, offset=0)
