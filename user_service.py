
from database import get_connection
from security import hash_password


def create_user(
    username: str,
    email: str,
    password: str,
    role: str = "viewer",
) -> dict:
    """Create a user account with a securely hashed password."""

    password_hash = hash_password(password)

    query = """
        INSERT INTO users (
            username,
            email,
            password_hash,
            role
        )
        VALUES (%s, %s, %s, %s)
        RETURNING id, username, email, role, is_active, created_at;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                query,
                (username, email, password_hash, role),
            )

            row = cur.fetchone()

    return {
        "id": row[0],
        "username": row[1],
        "email": row[2],
        "role": row[3],
        "is_active": row[4],
        "created_at": row[5],
    }

def get_user_by_username(username: str) -> dict | None:
    """Retrieve a user account by username for authentication."""

    query = """
        SELECT
            id,
            username,
            email,
            password_hash,
            role,
            is_active,
            created_at
        FROM users
        WHERE username = %s;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (username,))
            row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "username": row[1],
        "email": row[2],
        "password_hash": row[3],
        "role": row[4],
        "is_active": row[5],
        "created_at": row[6],
    }

def get_user_by_id(user_id: int) -> dict | None:
    """Retrieve a user account by its ID."""

    query = """
        SELECT
            id,
            username,
            email,
            role,
            is_active,
            created_at
        FROM users
        WHERE id = %s;
    """

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (user_id,))
            row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "username": row[1],
        "email": row[2],
        "role": row[3],
        "is_active": row[4],
        "created_at": row[5],
    }
