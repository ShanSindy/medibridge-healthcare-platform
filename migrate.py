from pathlib import Path

from database import get_connection


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def apply_migrations():
    with get_connection() as conn:
        with conn.cursor() as cursor:
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
