"""
Provider persistence.

Reads (get_all_providers, get_provider_by_id, search_providers) use the shared
SQLAlchemy engine through db_engine.read_only_connection(): PostgreSQL read-only
transactions with the engine's statement and lock timeouts. Writes (create, update,
delete) still use database.get_connection(), unchanged; the providers router translates
their psycopg constraint errors.
"""

from sqlalchemy import bindparam, column, func, select, table

from database import get_connection
from db_engine import read_only_connection
from models import ProviderCreate

# The columns the reads select, in the order of the returned dictionaries. Untyped, so
# values come back exactly as psycopg returns them, as before.
PROVIDERS = table(
    "providers",
    column("id"),
    column("first_name"),
    column("last_name"),
    column("specialty"),
    column("email"),
    column("phone"),
    column("created_at"),
)


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
    with read_only_connection() as conn:
        rows = conn.execute(select(PROVIDERS).order_by(PROVIDERS.c.id)).mappings().all()

    return [dict(row) for row in rows]


def get_provider_by_id(provider_id: int) -> dict | None:
    """
    Retrieve a healthcare provider by ID.
    Returns None if the provider does not exist.
    """
    query = select(PROVIDERS).where(PROVIDERS.c.id == bindparam("provider_id"))

    with read_only_connection() as conn:
        row = conn.execute(query, {"provider_id": provider_id}).mappings().one_or_none()

    return dict(row) if row is not None else None


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
    specialty: str | None = None,
) -> tuple[list[dict], int]:
    """
    Search providers with filters and database pagination.

    Each given filter is a case-insensitive prefix match (ILIKE), with `%`, `_` and backslash
    in the value matched literally. Returns one page ordered by id and the total number
    of matches. Both queries run in one read-only READ COMMITTED transaction, as before:
    each sees the data committed when it starts, so a provider created or deleted between
    them can make the total differ from what the page shows.
    """

    def prefix_pattern(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"{escaped}%"

    conditions = []
    params: dict[str, object] = {}

    if family is not None:
        conditions.append(PROVIDERS.c.last_name.ilike(bindparam("family")))
        params["family"] = prefix_pattern(family)

    if given is not None:
        conditions.append(PROVIDERS.c.first_name.ilike(bindparam("given")))
        params["given"] = prefix_pattern(given)

    if specialty is not None:
        conditions.append(PROVIDERS.c.specialty.ilike(bindparam("specialty")))
        params["specialty"] = prefix_pattern(specialty)

    page = (
        select(PROVIDERS)
        .where(*conditions)
        .order_by(PROVIDERS.c.id)
        .limit(bindparam("limit"))
        .offset(bindparam("offset"))
    )
    matches = select(func.count()).select_from(PROVIDERS).where(*conditions)

    with read_only_connection() as conn:
        rows = conn.execute(page, {**params, "limit": count, "offset": offset}).mappings().all()
        total = conn.execute(matches, params).scalar_one()

    return [dict(row) for row in rows], total
