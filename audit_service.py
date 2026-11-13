
from database import get_connection


def get_audit_logs(
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """Retrieve paginated audit event metadata."""

    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    if offset < 0:
        raise ValueError("offset must be non-negative")

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    user_id,
                    action,
                    resource_type,
                    resource_id,
                    outcome,
                    created_at
                FROM audit_logs
                ORDER BY created_at DESC, id DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )

            columns = [
                column.name
                for column in cursor.description
            ]

            return [
                dict(zip(columns, row))
                for row in cursor.fetchall()
            ]
