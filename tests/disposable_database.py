"""
Lifecycle of one disposable PostgreSQL test database: create, clean, drop.

Every step that can destroy data first proves, through tests/db_safety.py,
that the target is the exact database this object created.
"""

import sys

import psycopg
from psycopg import sql

from tests import db_safety


def release_application_pool() -> None:
    """
    Closes the idle connections of the application's shared engine (db_engine.py), which
    points at the session database. Imports nothing: if no test imported db_engine, there
    is no pool to close.
    """
    db_engine = sys.modules.get("db_engine")
    if db_engine is not None:
        db_engine.dispose_engine()


class DisposableDatabase:
    def __init__(self, config: db_safety.TestDatabaseConfig, name: str):
        if not db_safety.is_disposable_name(name):
            raise db_safety.UnsafeTestDatabase("Not a disposable test database name.")
        self.config = config
        self.name = name
        # Set once CREATE DATABASE has been sent: from then on a database
        # with this name may exist and must be accounted for.
        self.create_attempted = False
        # Identity recorded right after this process's own CREATE DATABASE.
        self.created: db_safety.CreatedDatabase | None = None
        self.marker_written = False
        self.dropped = False
        self.drop_error: str | None = None
        self.cleanup_connection: psycopg.Connection | None = None

    # --- connections ---------------------------------------------------------------

    def _connect(self, dbname: str) -> psycopg.Connection:
        """
        An autocommit connection, returned only after checking that it reached the
        configured server (db_safety.check_destination); closed otherwise.
        """
        conn = psycopg.connect(**self.config.connect_kwargs(dbname), autocommit=True)
        try:
            db_safety.check_destination(conn.info, self.config.host)
        except BaseException:
            conn.close()
            raise
        return conn

    def admin_connection(self) -> psycopg.Connection:
        return self._connect(self.config.maintenance_db)

    def role_status(self) -> tuple[bool, bool, bool]:
        """(is_superuser, can_create_db, in_recovery) for the configured role."""
        with self.admin_connection() as admin:
            return admin.execute(
                "SELECT rolsuper, rolcreatedb, pg_is_in_recovery() "
                "FROM pg_roles WHERE rolname = current_user"
            ).fetchone()

    def abandoned_databases(self) -> list[str]:
        with self.admin_connection() as admin:
            return db_safety.find_abandoned_databases(admin, self.name)

    # --- create -----------------------------------------------------------------------

    def create(self) -> None:
        with self.admin_connection() as admin:
            self.create_attempted = True
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.name)))
            self.created = self._record_identity(admin)
            self._write_marker(admin)
            self.marker_written = True

    def _record_identity(self, admin: psycopg.Connection) -> db_safety.CreatedDatabase:
        found, current_role_oid = db_safety.fetch_identity(admin, self.name)
        if found is None or found.owner_oid != current_role_oid:
            raise db_safety.UnsafeTestDatabase(
                "Could not confirm that the new test database is owned by the test role."
            )
        return db_safety.CreatedDatabase(name=self.name, oid=found.oid, owner_oid=found.owner_oid)

    def _write_marker(self, admin: psycopg.Connection) -> None:
        admin.execute(
            sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(self.name), sql.Literal(db_safety.OWNERSHIP_MARKER)
            )
        )

    # --- per-test cleanup -----------------------------------------------------------

    def open_cleanup_connection(self) -> None:
        conn = self._connect(self.name)
        # Fail fast instead of hanging if a test leaves a transaction open.
        conn.execute("SET lock_timeout = '5s'")
        self.cleanup_connection = conn
        self._verify_connected()

    def _verify_connected(self) -> None:
        conn = self.cleanup_connection
        if conn is None or conn.execute("SELECT current_database()").fetchone()[0] != self.name:
            raise db_safety.UnsafeTestDatabase(
                "The cleanup connection is not attached to this session's test database."
            )
        found, current_role_oid = db_safety.fetch_identity(conn, self.name)
        db_safety.check_identity(
            session_name=self.name,
            created=self.created,
            found=found,
            current_role_oid=current_role_oid,
            require_marker=True,
        )

    def clean(self) -> None:
        """
        Empties every application table. Failures are raised, never ignored.

        The table list is read from the catalog each time, so tables created
        after the session started are included, and tables in any schema
        other than `public` stop the run instead of being skipped. TRUNCATE
        is skipped when every table is already empty.
        """
        self._verify_connected()
        conn = self.cleanup_connection
        tables = db_safety.cleanup_targets(conn.execute(db_safety.TABLES_QUERY).fetchall())
        identifiers = [sql.Identifier(table) for table in tables]
        has_rows = sql.SQL("SELECT {}").format(
            sql.SQL(" OR ").join(
                sql.SQL("EXISTS (SELECT 1 FROM {})").format(name) for name in identifiers
            )
        )
        if conn.execute(has_rows).fetchone()[0]:
            conn.execute(sql.SQL("TRUNCATE {} CASCADE").format(sql.SQL(", ").join(identifiers)))

    # --- drop ------------------------------------------------------------------------

    def drop(self) -> None:
        """
        Drops the database if, and only if, it is provably the one this object
        created. Otherwise records why and raises; nothing is dropped.

        For the session database, the application's pooled connections to it are
        closed first, so ending them does not depend on WITH (FORCE). A failure there
        is not a reason to keep the database: the identity-checked drop still runs
        (WITH (FORCE) ends any connection left open), and the failure is recorded and
        raised afterwards.
        """
        if self.cleanup_connection is not None:
            self.cleanup_connection.close()
            self.cleanup_connection = None
        if not self.create_attempted or self.dropped:
            return
        release_error = None
        if db_safety.is_session_database(self.name):
            try:
                release_application_pool()
            except Exception as exc:
                release_error = exc
        try:
            with self.admin_connection() as admin:
                found, current_role_oid = db_safety.fetch_identity(admin, self.name)
                db_safety.check_identity(
                    session_name=self.name,
                    created=self.created,
                    found=found,
                    current_role_oid=current_role_oid,
                    require_marker=self.marker_written,
                )
                admin.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(self.name))
                )
        except Exception as exc:
            self.drop_error = f"{type(exc).__name__}: {exc}"
            if release_error is not None:
                exc.add_note(f"Releasing the application pool also failed: {release_error!r}")
            raise
        self.dropped = True
        if release_error is not None:
            self.drop_error = (
                f"pool release failed: {type(release_error).__name__}: {release_error}"
            )
            raise release_error
