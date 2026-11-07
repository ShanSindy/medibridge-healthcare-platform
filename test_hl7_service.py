
from uuid import uuid4
from database import get_connection
from hl7_service import register_patient_from_hl7
import pytest
from unittest.mock import patch


def test_register_patient_from_hl7():
    mrn = f"MRN-TEST-{uuid4().hex[:12]}"

    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A04|MSG00007|P|2.5\r"
        "EVN|A04|20261107120000\r"
        f"PID|1||{mrn}^^^HOSPITAL^MR||Smith^Jordan||19950722|F"
    )

    patient_id = None

    try:
        result = register_patient_from_hl7(message)

        patient_id = result["patient_id"]

        assert result["status"] == "created"
        assert result["mrn"] == mrn

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.first_name, p.last_name,
                           p.date_of_birth, p.gender, pi.value
                    FROM patients p
                    JOIN patient_identifiers pi
                        ON pi.patient_id = p.id
                    WHERE p.id = %s
                    """,
                    (patient_id,)
                )

                row = cur.fetchone()

        assert row is not None
        assert row[0] == "Jordan"
        assert row[1] == "Smith"
        assert str(row[2]) == "1995-07-22"
        assert row[3] == "female"
        assert row[4] == mrn

    finally:
        if patient_id is not None:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM patients WHERE id = %s",
                        (patient_id,)
                    )

def test_register_patient_from_hl7_duplicate_mrn():
    mrn = f"MRN-TEST-{uuid4().hex[:12]}"

    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A04|MSG00008|P|2.5\r"
        "EVN|A04|20261107120000\r"
        f"PID|1||{mrn}^^^HOSPITAL^MR||Smith^Jordan||19950722|F"
    )

    patient_id = None

    try:
        first_result = register_patient_from_hl7(message)
        patient_id = first_result["patient_id"]

        second_result = register_patient_from_hl7(message)

        assert first_result["status"] == "created"
        assert second_result["status"] == "already_exists"

        assert first_result["patient_id"] == second_result["patient_id"]

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM patient_identifiers
                    WHERE system = %s AND value = %s
                    """,
                    ("https://medibridge.example.org/mrn", mrn)
                )

                count = cur.fetchone()[0]

        assert count == 1

    finally:
        if patient_id is not None:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM patients WHERE id = %s",
                        (patient_id,)
                    )


def test_register_patient_from_hl7_transaction_rollback():
    unique_id = uuid4().hex[:12]
    mrn = f"MRN-TEST-{unique_id}"
    unique_last_name = f"Rollback{unique_id}"

    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261107120000||ADT^A04|MSG00009|P|2.5\r"
        "EVN|A04|20261107120000\r"
        f"PID|1||{mrn}^^^HOSPITAL^MR||"
        f"{unique_last_name}^Jordan||19950722|F"
    )

    # Force the identifier insertion to fail.
    with patch("hl7_service.HL7_MRN_SYSTEM", None):
        with pytest.raises(Exception):
            register_patient_from_hl7(message)

    # Confirm neither record was committed.
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM patient_identifiers
                WHERE value = %s
                """,
                (mrn,)
            )
            identifier_count = cur.fetchone()[0]

            cur.execute(
                """
                SELECT COUNT(*)
                FROM patients
                WHERE first_name = %s
                  AND last_name = %s
                """,
                ("Jordan", unique_last_name)
            )
            patient_count = cur.fetchone()[0]

    assert identifier_count == 0
    assert patient_count == 0
