
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
