from database import get_connection


def get_processed_hl7_message(metadata: dict) -> dict | None:
    """
    Look up a previously processed HL7 message using
    its sending application, facility, and control ID.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, message_type, status
                FROM hl7_messages
                WHERE sending_application = %s
                  AND sending_facility = %s
                  AND message_control_id = %s
                  AND status = 'processed'
                """,
                (
                    metadata["sending_application"],
                    metadata["sending_facility"],
                    metadata["message_control_id"],
                ),
            )

            row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "message_type": row[1],
        "status": row[2],
    }


def record_hl7_message(cur, metadata: dict) -> bool:
    """
    Record an HL7 message using an existing database cursor.

    Returns True if a new message was inserted.
    Returns False if the sender/control ID already exists.

    The caller controls the transaction.
    """
    cur.execute(
        """
        INSERT INTO hl7_messages (
            sending_application,
            sending_facility,
            message_control_id,
            message_type,
            status
        )
        VALUES (%s, %s, %s, %s, 'processed')
        ON CONFLICT (
            sending_application,
            sending_facility,
            message_control_id
        )
        DO NOTHING
        RETURNING id
        """,
        (
            metadata["sending_application"],
            metadata["sending_facility"],
            metadata["message_control_id"],
            metadata["message_type"],
        ),
    )

    return cur.fetchone() is not None
