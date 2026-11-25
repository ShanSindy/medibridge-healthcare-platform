from sqlalchemy import text

from db_engine import read_only_connection

# The pre-Phase 2 query, with named bind parameters in place of %s placeholders.
AUDIT_LOG_PAGE = text(
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
    LIMIT :limit OFFSET :offset
    """
)


def get_audit_logs(
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    Retrieve paginated audit event metadata.

    Runs through db_engine.read_only_connection(): a pooled connection in a transaction
    that PostgreSQL keeps read-only, with the engine's statement and lock timeouts, rolled
    back when the block ends. Database errors propagate as SQLAlchemy `DBAPIError`
    subclasses, with the original psycopg error in `.orig`.
    """

    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    if offset < 0:
        raise ValueError("offset must be non-negative")

    with read_only_connection() as conn:
        rows = conn.execute(AUDIT_LOG_PAGE, {"limit": limit, "offset": offset}).mappings()
        return [dict(row) for row in rows]
