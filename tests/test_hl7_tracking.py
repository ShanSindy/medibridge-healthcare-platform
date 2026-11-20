from uuid import uuid4
from database import get_connection
from hl7_tracking import (
    get_processed_hl7_message,
    record_hl7_message,
)


def test_get_processed_hl7_message_not_found():
    metadata = {
        "sending_application": "HOSPITAL_APP",
        "sending_facility": "GENERAL_HOSPITAL",
        "message_control_id": f"TEST-{uuid4()}",
        "message_type": "ADT^A04",
    }

    result = get_processed_hl7_message(metadata)

    assert result is None


def test_get_processed_hl7_message_found():
    control_id = f"TEST-{uuid4()}"

    metadata = {
        "sending_application": "HOSPITAL_APP",
        "sending_facility": "GENERAL_HOSPITAL",
        "message_control_id": control_id,
        "message_type": "ADT^A04",
    }

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hl7_messages (
                        sending_application,
                        sending_facility,
                        message_control_id,
                        message_type,
                        status
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        metadata["sending_application"],
                        metadata["sending_facility"],
                        control_id,
                        metadata["message_type"],
                        "processed",
                    ),
                )

        result = get_processed_hl7_message(metadata)

        assert result is not None
        assert result["message_type"] == "ADT^A04"
        assert result["status"] == "processed"
        assert isinstance(result["id"], int)

    finally:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM hl7_messages
                    WHERE sending_application = %s
                      AND sending_facility = %s
                      AND message_control_id = %s
                    """,
                    (
                        metadata["sending_application"],
                        metadata["sending_facility"],
                        control_id,
                    ),
                )


def test_record_hl7_message_prevents_duplicates():
    control_id = f"TEST-{uuid4()}"

    metadata = {
        "sending_application": "HOSPITAL_APP",
        "sending_facility": "GENERAL_HOSPITAL",
        "message_control_id": control_id,
        "message_type": "ADT^A04",
    }

    with get_connection() as conn:
        try:
            with conn.cursor() as cur:
                first_result = record_hl7_message(cur, metadata)

                second_result = record_hl7_message(cur, metadata)

                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM hl7_messages
                    WHERE sending_application = %s
                      AND sending_facility = %s
                      AND message_control_id = %s
                    """,
                    (
                        metadata["sending_application"],
                        metadata["sending_facility"],
                        control_id,
                    ),
                )

                count = cur.fetchone()[0]

                assert first_result is True
                assert second_result is False
                assert count == 1

        finally:
            # Roll back test inserts to keep the database clean.
            conn.rollback()
