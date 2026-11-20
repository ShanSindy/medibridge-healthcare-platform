import json

from database import get_connection


def log_audit_event(
    action: str,
    resource_type: str,
    outcome: str,
    user_id: int | None = None,
    resource_id: str | None = None,
    details: dict | None = None,
    connection=None,
) -> None:
    """
    Record an audit event in PostgreSQL.

    When connection is provided, use the caller's
    transaction without committing it.

    Never include passwords, tokens, or sensitive
    patient information in audit details.
    """

    if outcome not in ("success", "failure"):
        raise ValueError("Audit outcome must be 'success' or 'failure'.")

    def insert_event(conn):
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO audit_logs (
                    user_id,
                    action,
                    resource_type,
                    resource_id,
                    outcome,
                    details
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    action,
                    resource_type,
                    resource_id,
                    outcome,
                    json.dumps(details) if details is not None else None,
                ),
            )

    if connection is not None:
        insert_event(connection)
    else:
        with get_connection() as conn:
            insert_event(conn)
            conn.commit()
