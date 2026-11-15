
from database import get_connection
from models import ProviderCreate


def create_provider(provider: ProviderCreate) -> dict:
    """
    Create a new healthcare provider in PostgreSQL.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO providers (
                    first_name,
                    last_name,
                    specialty,
                    email,
                    phone
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING
                    id,
                    first_name,
                    last_name,
                    specialty,
                    email,
                    phone,
                    created_at
                """,
                (
                    provider.first_name,
                    provider.last_name,
                    provider.specialty,
                    provider.email,
                    provider.phone,
                ),
            )

            row = cur.fetchone()

    return {
        "id": row[0],
        "first_name": row[1],
        "last_name": row[2],
        "specialty": row[3],
        "email": row[4],
        "phone": row[5],
        "created_at": row[6],
    }


def get_all_providers() -> list[dict]:
    """
    Retrieve all healthcare providers.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    first_name,
                    last_name,
                    specialty,
                    email,
                    phone,
                    created_at
                FROM providers
                ORDER BY id
                """
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "first_name": row[1],
            "last_name": row[2],
            "specialty": row[3],
            "email": row[4],
            "phone": row[5],
            "created_at": row[6],
        }
        for row in rows
    ]

def get_provider_by_id(provider_id: int) -> dict | None:
    """
    Retrieve a healthcare provider by ID.
    Returns None if the provider does not exist.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    first_name,
                    last_name,
                    specialty,
                    email,
                    phone,
                    created_at
                FROM providers
                WHERE id = %s
                """,
                (provider_id,),
            )

            row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "first_name": row[1],
        "last_name": row[2],
        "specialty": row[3],
        "email": row[4],
        "phone": row[5],
        "created_at": row[6],
    }

def update_provider(
    provider_id: int,
    provider: ProviderCreate,
) -> dict | None:
    """
    Update an existing healthcare provider.
    Returns None if the provider does not exist.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE providers
                SET
                    first_name = %s,
                    last_name = %s,
                    specialty = %s,
                    email = %s,
                    phone = %s
                WHERE id = %s
                RETURNING
                    id,
                    first_name,
                    last_name,
                    specialty,
                    email,
                    phone,
                    created_at
                """,
                (
                    provider.first_name,
                    provider.last_name,
                    provider.specialty,
                    provider.email,
                    provider.phone,
                    provider_id,
                ),
            )

            row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "first_name": row[1],
        "last_name": row[2],
        "specialty": row[3],
        "email": row[4],
        "phone": row[5],
        "created_at": row[6],
    }

def delete_provider(provider_id: int) -> bool:
    """
    Delete a healthcare provider by ID.

    Returns True if the provider was deleted.
    Returns False if the provider does not exist.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM providers
                WHERE id = %s
                RETURNING id
                """,
                (provider_id,),
            )

            deleted_row = cur.fetchone()

    return deleted_row is not None


def search_providers(
    family: str | None = None,
    given: str | None = None,
    count: int = 10,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Search providers with filters and database pagination."""

    def prefix_pattern(value: str) -> str:
        escaped = (
            value.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        return f"{escaped}%"

    conditions = []
    params = []

    if family is not None:
        conditions.append("last_name ILIKE %s")
        params.append(prefix_pattern(family))

    if given is not None:
        conditions.append("first_name ILIKE %s")
        params.append(prefix_pattern(given))

    where_clause = (
        "WHERE " + " AND ".join(conditions)
        if conditions
        else ""
    )

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    id, first_name, last_name,
                    specialty, email, phone, created_at
                FROM providers
                {where_clause}
                ORDER BY id
                LIMIT %s OFFSET %s
                """,
                (*params, count, offset),
            )
            rows = cur.fetchall()

            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM providers
                {where_clause}
                """,
                tuple(params),
            )
            total = cur.fetchone()[0]

    providers = [
        {
            "id": row[0],
            "first_name": row[1],
            "last_name": row[2],
            "specialty": row[3],
            "email": row[4],
            "phone": row[5],
            "created_at": row[6],
        }
        for row in rows
    ]

    return providers, total
