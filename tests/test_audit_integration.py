
import pytest

from audit import log_audit_event
from database import get_connection
from tests.db_safety import is_session_database


def test_audit_event_persists_in_postgresql():
    with get_connection() as conn:
        if not is_session_database(conn.info.dbname):
            raise RuntimeError(
                "Integration tests must use the disposable test database."
            )

        # Use a unique identifier without patient information.
        import uuid
        resource_id = str(uuid.uuid4())

        try:
            log_audit_event(
                action="test.audit.integration",
                resource_type="test",
                resource_id=resource_id,
                outcome="success",
                user_id=None,
                details={"source": "integration_test"},
                connection=conn,
            )

            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT action, resource_type, outcome, details
                    FROM audit_logs
                    WHERE resource_id = %s
                    """,
                    (resource_id,),
                )

                row = cursor.fetchone()

            assert row is not None
            assert row[0] == "test.audit.integration"
            assert row[1] == "test"
            assert row[2] == "success"
            assert row[3] == {"source": "integration_test"}

        finally:
            # Roll back all test changes, including the audit event.
            conn.rollback()

def test_audit_event_rolls_back_in_postgresql():
    import uuid

    resource_id = str(uuid.uuid4())

    with get_connection() as conn:
        if not is_session_database(conn.info.dbname):
            raise RuntimeError(
                "Integration tests must use the disposable test database."
            )

        log_audit_event(
            action="test.audit.rollback",
            resource_type="test",
            resource_id=resource_id,
            outcome="success",
            user_id=None,
            connection=conn,
        )

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM audit_logs
                WHERE resource_id = %s
                """,
                (resource_id,),
            )

            assert cursor.fetchone()[0] == 1

        # Explicitly discard the uncommitted audit event.
        conn.rollback()

    # Open a new connection to verify the event was not saved.
    with get_connection() as verification_conn:
        if not is_session_database(verification_conn.info.dbname):
            raise RuntimeError(
                "Verification must use the disposable test database."
            )

        with verification_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM audit_logs
                WHERE resource_id = %s
                """,
                (resource_id,),
            )

            assert cursor.fetchone()[0] == 0

def test_committed_audit_event_persists_in_postgresql():
    import uuid

    resource_id = str(uuid.uuid4())

    with get_connection() as conn:
        if not is_session_database(conn.info.dbname):
            raise RuntimeError(
                "Integration tests must use the disposable test database."
            )

        try:
            log_audit_event(
                action="test.audit.commit",
                resource_type="test",
                resource_id=resource_id,
                outcome="success",
                user_id=None,
                connection=conn,
            )

            conn.commit()

            # Verify persistence using a separate connection.
            with get_connection() as verification_conn:
                if not is_session_database(verification_conn.info.dbname):
                    raise RuntimeError(
                        "Verification must use the disposable test database."
                    )

                with verification_conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT action, resource_type, outcome
                        FROM audit_logs
                        WHERE resource_id = %s
                        """,
                        (resource_id,),
                    )

                    row = cursor.fetchone()

                assert row == (
                    "test.audit.commit",
                    "test",
                    "success",
                )

        finally:
            # Remove only this test's audit event.
            with get_connection() as cleanup_conn:
                if not is_session_database(cleanup_conn.info.dbname):
                    raise RuntimeError(
                        "Cleanup must use the disposable test database."
                    )

                with cleanup_conn.cursor() as cursor:
                    cursor.execute(
                        """
                        DELETE FROM audit_logs
                        WHERE resource_id = %s
                          AND action = %s
                          AND resource_type = %s
                        """,
                        (
                            resource_id,
                            "test.audit.commit",
                            "test",
                        ),
                    )

def test_patient_creation_and_audit_roll_back_together():
    import uuid

    resource_marker = str(uuid.uuid4())

    with get_connection() as conn:
        if not is_session_database(conn.info.dbname):
            raise RuntimeError(
                "Integration tests must use the disposable test database."
            )

        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO patients (
                    first_name,
                    last_name,
                    date_of_birth,
                    gender
                )
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (
                    "AuditTest",
                    resource_marker,
                    "1990-05-12",
                    "other",
                ),
            )

            patient_id = cursor.fetchone()[0]

        log_audit_event(
            action="patient.create",
            resource_type="patient",
            resource_id=str(patient_id),
            outcome="success",
            user_id=None,
            connection=conn,
        )

        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM patients WHERE id = %s",
                (patient_id,),
            )
            assert cursor.fetchone()[0] == 1

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM audit_logs
                WHERE action = %s
                  AND resource_type = %s
                  AND resource_id = %s
                """,
                ("patient.create", "patient", str(patient_id)),
            )
            assert cursor.fetchone()[0] == 1

        # Discard both operations in the same transaction.
        conn.rollback()

    with get_connection() as verification_conn:
        if not is_session_database(verification_conn.info.dbname):
            raise RuntimeError(
                "Verification must use the disposable test database."
            )

        with verification_conn.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM patients WHERE id = %s",
                (patient_id,),
            )
            assert cursor.fetchone()[0] == 0

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM audit_logs
                WHERE action = %s
                  AND resource_type = %s
                  AND resource_id = %s
                """,
                ("patient.create", "patient", str(patient_id)),
            )
            assert cursor.fetchone()[0] == 0

def test_patient_creation_rolls_back_after_real_audit_failure():
    import uuid

    marker = str(uuid.uuid4())
    patient_id = None

    with pytest.raises(ValueError, match="Audit outcome"):
        with get_connection() as conn:
            if not is_session_database(conn.info.dbname):
                raise RuntimeError(
                    "Integration tests must use the disposable test database."
                )

            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO patients (
                        first_name,
                        last_name,
                        date_of_birth,
                        gender
                    )
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        "AuditFailureTest",
                        marker,
                        "1990-05-12",
                        "other",
                    ),
                )

                patient_id = cursor.fetchone()[0]

            # Deliberately trigger audit validation failure.
            log_audit_event(
                action="patient.create",
                resource_type="patient",
                resource_id=str(patient_id),
                outcome="invalid_outcome",
                user_id=None,
                connection=conn,
            )

    assert patient_id is not None

    # The connection context should have rolled back the patient insert.
    with get_connection() as verification_conn:
        if not is_session_database(verification_conn.info.dbname):
            raise RuntimeError(
                "Verification must use the disposable test database."
            )

        with verification_conn.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM patients WHERE id = %s",
                (patient_id,),
            )

            assert cursor.fetchone()[0] == 0

def test_patient_creation_rolls_back_on_audit_constraint_violation():
    import uuid
    import psycopg

    marker = str(uuid.uuid4())
    patient_id = None

    with pytest.raises(psycopg.errors.CheckViolation):
        with get_connection() as conn:
            if not is_session_database(conn.info.dbname):
                raise RuntimeError(
                    "Integration tests must use the disposable test database."
                )

            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO patients (
                        first_name,
                        last_name,
                        date_of_birth,
                        gender
                    )
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        "AuditConstraintTest",
                        marker,
                        "1990-05-12",
                        "other",
                    ),
                )

                patient_id = cursor.fetchone()[0]

                # Bypass application validation to test
                # PostgreSQL's actual CHECK constraint.
                cursor.execute(
                    """
                    INSERT INTO audit_logs (
                        user_id,
                        action,
                        resource_type,
                        resource_id,
                        outcome
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        None,
                        "patient.create",
                        "patient",
                        str(patient_id),
                        "invalid_outcome",
                    ),
                )

    assert patient_id is not None

    # The failed audit INSERT must roll back the patient INSERT.
    with get_connection() as verification_conn:
        if not is_session_database(verification_conn.info.dbname):
            raise RuntimeError(
                "Verification must use the disposable test database."
            )

        with verification_conn.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM patients WHERE id = %s",
                (patient_id,),
            )
            assert cursor.fetchone()[0] == 0

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM audit_logs
                WHERE action = %s
                  AND resource_type = %s
                  AND resource_id = %s
                """,
                (
                    "patient.create",
                    "patient",
                    str(patient_id),
                ),
            )
            assert cursor.fetchone()[0] == 0
