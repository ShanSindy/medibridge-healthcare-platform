from pathlib import Path

from database import get_connection
from schema_inspection import ALEMBIC_TABLE, MIGRATION_LOCK_KEY

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class MigrationRefused(Exception):
    """The historical migrations were refused before any change was made."""


def apply_migrations():
    with get_connection() as conn:
        with conn.cursor() as cursor:
            # The same advisory lock as alembic/env.py, held for this whole session, so
            # migrate.py and Alembic never run against one database at the same time. It
            # is released when the connection closes, also after an error.
            cursor.execute("SELECT pg_try_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
            if not cursor.fetchone()[0]:
                raise MigrationRefused("Another migration holds the MediBridge migration lock.")
            # A database built or managed by Alembic has no complete historical ledger;
            # running the historical files there would half-apply them.
            cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{ALEMBIC_TABLE}",))
            if cursor.fetchone()[0]:
                raise MigrationRefused(
                    "This database is managed by Alembic; migrate.py does not run on it."
                )
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

        conn.commit()

        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            with conn.transaction():
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT 1 FROM schema_migrations WHERE filename = %s",
                        (migration.name,),
                    )

                    if cursor.fetchone():
                        print(f"Skipping {migration.name}")
                        continue

                    print(f"Applying {migration.name}")

                    cursor.execute(migration.read_text(encoding="utf-8-sig"))

                    cursor.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (%s)",
                        (migration.name,),
                    )

    print("Database migrations complete.")


if __name__ == "__main__":
    apply_migrations()
