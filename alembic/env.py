"""
Alembic migration environment (Phase 2 Milestone 6 candidate; ADR 0003).

Supported: upgrading a fresh, empty database to the baseline, or re-running an upgrade
on a database already at the baseline (a no-op). Everything else is refused before any
change, inside the same transaction that would have run the migration:

- Only one migration runs at a time: the transaction takes a PostgreSQL advisory lock
  with pg_try_advisory_xact_lock and fails immediately if another session holds it.
- The connected database must be the expected one (current_database()).
- The public schema must be empty, or hold exactly the Alembic baseline. A database with
  the historical migrate.py ledger, application tables without a ledger, or anything
  else is refused: adopting or stamping an existing database is not implemented here.

The whole upgrade is one transaction (PostgreSQL DDL is transactional): a failure rolls
back every change, including the version row. Offline (SQL script) mode is refused.

Connection: tests pass an open SQLAlchemy Connection in config.attributes["connection"]
with config.attributes["database_name"]. Otherwise the engine is built from the DB_*
environment settings, as the application's shared engine is, with its statement and
lock timeouts. No URL or credentials are read from alembic.ini or written to output.
"""

import os

import db_schema
import schema_inspection
from alembic import context

target_metadata = db_schema.metadata


class MigrationRefused(Exception):
    """The migration was refused before any change was made."""


def _require_supported_state(connection, database_name: str) -> None:
    raw = connection.connection.driver_connection  # the same session and transaction
    locked = raw.execute(
        "SELECT pg_try_advisory_xact_lock(%s)", (schema_inspection.MIGRATION_LOCK_KEY,)
    )
    if not locked.fetchone()[0]:
        raise MigrationRefused("Another migration holds the MediBridge migration lock.")
    if raw.execute("SELECT current_database()").fetchone()[0] != database_name:
        raise MigrationRefused("The connection is not attached to the expected database.")
    fresh = schema_inspection.is_empty(raw)
    # From here on, and for every migration statement, unqualified names resolve in public
    # only, whatever the role's or session's search_path (transaction-local setting).
    raw.execute("SELECT set_config('search_path', 'public', true)")
    if fresh:
        return
    present = schema_inspection.public_relations(raw)
    # A re-run on a database already at the baseline is a no-op; this checks the relation
    # names, the version row and the environment only. Full equivalence is checked by
    # schema_inspection.inspect_migration_state.
    if present == set(db_schema.metadata.tables) | {
        schema_inspection.ALEMBIC_TABLE
    } and not schema_inspection.environment_problems(raw):
        if schema_inspection.alembic_table_problems(raw):
            raise MigrationRefused("alembic_version does not have the expected structure.")
        versions = [r[0] for r in raw.execute("SELECT version_num FROM alembic_version")]
        if versions == [schema_inspection.ALEMBIC_BASELINE]:
            return  # already at the baseline: the upgrade is a no-op
    raise MigrationRefused(
        "The database is not empty and not at the Alembic baseline; adopting an existing "
        "database is not supported."
    )


def _run(connection, database_name: str) -> None:
    if connection.in_transaction():
        raise MigrationRefused("The connection must not already be in a transaction.")
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        transaction_per_migration=False,
    )
    with context.begin_transaction():
        _require_supported_state(connection, database_name)
        context.run_migrations()
        # Before anything commits: the result must match the metadata exactly (ADR 0002
        # comparison), whether the baseline was just built or was already there. A version
        # row alone is never taken as proof of the schema.
        raw = connection.connection.driver_connection
        found = schema_inspection.verify_against_metadata(raw, db_schema.metadata)
        if found:
            raise MigrationRefused(
                f"The schema does not match the metadata ({len(found)} differences); "
                "nothing was committed."
            )


def run_migrations_online() -> None:
    attributes = context.config.attributes
    if "connection" in attributes:
        _run(attributes["connection"], attributes["database_name"])
        return

    import db_engine  # only for command-line use; tests pass their own connection

    # The operator names the target explicitly: alembic -x expected_database=NAME upgrade
    # head. It must match both DB_NAME and the database actually reached.
    expected = context.get_x_argument(as_dictionary=True).get("expected_database")
    if not expected or expected != os.environ.get("DB_NAME"):
        raise MigrationRefused("Pass -x expected_database=<name> matching DB_NAME.")
    engine = db_engine.build_engine(os.environ)
    try:
        with engine.connect() as connection:
            _run(connection, expected)
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise MigrationRefused("Offline SQL generation is not supported.")
run_migrations_online()
