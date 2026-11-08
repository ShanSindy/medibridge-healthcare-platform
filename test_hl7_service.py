
from uuid import uuid4
from database import get_connection
from hl7_service import register_patient_from_hl7, update_patient_from_hl7, HL7_MRN_SYSTEM
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


def test_register_patient_rejects_a08():
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261108120000||ADT^A08|MSG00022|P|2.5\r"
        "EVN|A08|20261108120000\r"
        "PID|1||MRN-000022^^^HOSPITAL^MR||"
        "Taylor^Jordan||19950722|F"
    )

    with pytest.raises(
        ValueError,
        match=r"Patient registration requires an ADT\^A04 message\."
    ):
        register_patient_from_hl7(message)

def test_update_patient_from_hl7():
    mrn = "MRN-A08-TEST-001"
    patient_id = None

    try:
        # Create a synthetic patient in the test database.
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO patients
                        (first_name, last_name, date_of_birth, gender)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    ("Jordan", "Taylor", "1995-07-22", "female")
                )
                patient_id = cur.fetchone()[0]

                cur.execute(
                    """
                    INSERT INTO patient_identifiers
                        (patient_id, system, value)
                    VALUES (%s, %s, %s)
                    """,
                    (patient_id, HL7_MRN_SYSTEM, mrn)
                )

        # Send an A08 message with updated demographics.
        message = (
            "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
            "20261108120000||ADT^A08|MSG00023|P|2.5\r"
            "EVN|A08|20261108120000\r"
            "PID|1||MRN-A08-TEST-001^^^HOSPITAL^MR||"
            "Taylor^Jordan||19950722|M"
        )

        result = update_patient_from_hl7(message)

        assert result["status"] == "updated"
        assert result["patient_id"] == patient_id
        assert result["mrn"] == mrn

        # Verify the existing database record was updated.
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT first_name, last_name, gender
                    FROM patients
                    WHERE id = %s
                    """,
                    (patient_id,)
                )
                updated_patient = cur.fetchone()

                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM patient_identifiers
                    WHERE system = %s AND value = %s
                    """,
                    (HL7_MRN_SYSTEM, mrn)
                )
                identifier_count = cur.fetchone()[0]

        assert updated_patient == ("Jordan", "Taylor", "male")
        assert identifier_count == 1

    finally:
        # Remove only the patient created by this test.
        if patient_id is not None:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM patients WHERE id = %s",
                        (patient_id,)
                    )

def test_update_patient_from_hl7_unknown_mrn():
    mrn = "MRN-A08-UNKNOWN-999"

    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261108130000||ADT^A08|MSG00024|P|2.5\r"
        "EVN|A08|20261108130000\r"
        "PID|1||MRN-A08-UNKNOWN-999^^^HOSPITAL^MR||"
        "Taylor^Jordan||19950722|F"
    )

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM patient_identifiers
                WHERE system = %s AND value = %s
                """,
                (HL7_MRN_SYSTEM, mrn)
            )
            assert cur.fetchone()[0] == 0

    with pytest.raises(
        ValueError,
        match="Cannot update patient: MRN not found."
    ):
        update_patient_from_hl7(message)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM patient_identifiers
                WHERE system = %s AND value = %s
                """,
                (HL7_MRN_SYSTEM, mrn)
            )
            assert cur.fetchone()[0] == 0

def test_update_patient_rejects_a04():
    message = (
        "MSH|^~\\&|REGISTRATION|HOSPITAL|MEDIBRIDGE|HOSPITAL|"
        "20261108140000||ADT^A04|MSG00025|P|2.5\r"
        "EVN|A04|20261108140000\r"
        "PID|1||MRN-A04-TEST-025^^^HOSPITAL^MR||"
        "Taylor^Jordan||19950722|F"
    )

    with pytest.raises(
        ValueError,
        match=r"Patient updates require an ADT\^A08 message\."
    ):
        update_patient_from_hl7(message)
