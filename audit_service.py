from sqlalchemy import text

from db_engine import get_engine

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

    Runs on a pooled connection from the shared engine (db_engine), in a transaction that
    PostgreSQL itself keeps read-only (`postgresql_readonly`; any write fails with SQLSTATE
    25006). The transaction is rolled back when the block ends, and the pool resets the
    read-only setting before the connection is reused. Database errors propagate as
    SQLAlchemy `DBAPIError` subclasses, with the original psycopg error in `.orig`.
    """

    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    if offset < 0:
        raise ValueError("offset must be non-negative")

    with get_engine().connect() as conn:
        conn.execution_options(postgresql_readonly=True)
        rows = conn.execute(AUDIT_LOG_PAGE, {"limit": limit, "offset": offset}).mappings()
        return [dict(row) for row in rows]
