"""Unit tests for mapping PostgreSQL constraint violations to HTTP errors."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from psycopg import errors

from db_errors import raise_for_constraint


class FakeViolation(errors.ForeignKeyViolation):
    """A ForeignKeyViolation whose diagnostics can be set without a database."""

    def __init__(self, constraint_name: str | None):
        super().__init__("synthetic violation")
        self._fake_diag = SimpleNamespace(constraint_name=constraint_name)

    @property
    def diag(self):  # type: ignore[override]
        return self._fake_diag


KNOWN = {"appointments_patient_id_fkey": (404, "Patient not found")}


def test_known_constraint_becomes_the_mapped_http_error():
    violation = FakeViolation("appointments_patient_id_fkey")

    with pytest.raises(HTTPException) as caught:
        raise_for_constraint(violation, KNOWN)

    assert caught.value.status_code == 404
    assert caught.value.detail == "Patient not found"
    assert caught.value.__cause__ is violation


@pytest.mark.parametrize("constraint_name", ["some_other_constraint", None])
def test_unknown_constraint_is_reraised_unchanged(constraint_name):
    violation = FakeViolation(constraint_name)

    with pytest.raises(errors.ForeignKeyViolation) as caught:
        raise_for_constraint(violation, KNOWN)

    assert caught.value is violation
