"""
Read-only inspection of a MediBridge database schema and its migration state.

Used by the schema drift tests and by the Alembic migration environment. It never opens
a connection or creates an engine: every function takes a psycopg connection that the
caller owns, already verified as the intended database, inside a transaction the caller
controls. Only materialize_expected() writes, and only into a new scratch schema, for a
caller that rolls the transaction back.

The comparison semantics are those of ADR 0002: definitions come from PostgreSQL's own
deparsers with search_path set to "<schema under test>, public", so a reference to any
other schema prints qualified and shows up as a difference. Error messages name objects
and states only; they never include connection settings or credentials.
"""

from dataclasses import dataclass

from sqlalchemy.dialects.postgresql import psycopg as pg_dialect
from sqlalchemy.schema import CreateIndex, CreateTable

import db_schema


class UnsafeDatabaseState(Exception):
    """The connection is not attached to the expected database, or its state is unsafe."""


# The historical migration ledger written by migrate.py, in application order.
HISTORICAL_MIGRATIONS = (
    "000_create_patients.sql",
    "001_create_patient_identifiers.sql",
    "002_create_hl7_messages.sql",
    "003_create_providers.sql",
    "004_create_appointments.sql",
    "005_prevent_appointment_overlaps.sql",
    "006_create_users.sql",
    "007_create_audit_logs.sql",
)
LEDGER_TABLE = "schema_migrations"
ALEMBIC_TABLE = "alembic_version"
ALEMBIC_BASELINE = "0001_baseline"
# The PostgreSQL advisory lock key that serializes migrations (any fixed bigint).
MIGRATION_LOCK_KEY = 4_812_115_501


def rows(conn, query, params=None):
    return conn.execute(query, params).fetchall()


def snapshot(conn, schema: str, exclude_tables=()) -> dict:
    """Every compared property of `schema`, as plain data. Run inside a transaction."""
    conn.execute("SELECT set_config('search_path', %s, true)", (f'"{schema}", public',))
    excluded = sorted(exclude_tables)
    ns = rows(conn, "SELECT oid FROM pg_namespace WHERE nspname = %s", (schema,))[0][0]
    not_extension = """NOT EXISTS (SELECT 1 FROM pg_depend d
        WHERE d.objid = {oid} AND d.deptype = 'e')"""

    objects = rows(
        conn,
        f"""SELECT c.relkind::text, c.relname FROM pg_class c
            WHERE c.relnamespace = %s AND {not_extension.format(oid="c.oid")}
              AND NOT (c.relkind = 'r' AND c.relname = ANY(%s))
              AND NOT EXISTS (SELECT 1 FROM pg_index x JOIN pg_class t ON t.oid = x.indrelid
                              WHERE x.indexrelid = c.oid AND t.relname = ANY(%s))
            ORDER BY 1, 2""",  # noqa: S608 - only the fixed NOT EXISTS fragment is inserted
        (ns, excluded, excluded),
    )
    functions = rows(
        conn,
        f"""SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')'
            FROM pg_proc p WHERE p.pronamespace = %s AND {not_extension.format(oid="p.oid")}
            ORDER BY 1""",  # noqa: S608 - only the fixed NOT EXISTS fragment is inserted
        (ns,),
    )
    types = rows(
        conn,
        f"""SELECT t.typname FROM pg_type t
            WHERE t.typnamespace = %s AND {not_extension.format(oid="t.oid")}
              AND t.typrelid = 0 AND t.typelem = 0
            ORDER BY 1""",  # noqa: S608 - fixed fragment only
        (ns,),
    )
    tables = [
        r[0]
        for r in rows(
            conn,
            "SELECT relname FROM pg_class WHERE relnamespace = %s AND relkind = 'r' "
            "AND NOT relname = ANY(%s) ORDER BY 1",
            (ns, excluded),
        )
    ]

    columns = rows(
        conn,
        """SELECT c.relname, row_number() OVER (PARTITION BY a.attrelid ORDER BY a.attnum),
                  a.attname, format_type(a.atttypid, a.atttypmod),
                  a.attnotnull, a.attidentity::text, a.attgenerated::text,
                  pg_get_expr(d.adbin, d.adrelid), cn.nspname || '.' || co.collname
           FROM pg_attribute a
           JOIN pg_class c ON c.oid = a.attrelid
           LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
           LEFT JOIN pg_collation co ON co.oid = a.attcollation AND a.attcollation <> 100
           LEFT JOIN pg_namespace cn ON cn.oid = co.collnamespace
           WHERE c.relnamespace = %s AND c.relkind = 'r' AND NOT c.relname = ANY(%s)
             AND a.attnum > 0 AND NOT a.attisdropped
           ORDER BY 1, 2""",
        (ns, excluded),
    )
    sequences = rows(
        conn,
        """SELECT c.relname, a.attname, format_type(s.seqtypid, NULL), s.seqstart,
                  s.seqincrement, s.seqmin, s.seqmax, s.seqcache, s.seqcycle,
                  sc.relnamespace = %s
           FROM pg_attribute a
           JOIN pg_class c ON c.oid = a.attrelid
           JOIN pg_depend dep ON dep.refobjid = c.oid AND dep.refobjsubid = a.attnum
                AND dep.classid = 'pg_class'::regclass AND dep.deptype = 'i'
           JOIN pg_class sc ON sc.oid = dep.objid AND sc.relkind = 'S'
           JOIN pg_sequence s ON s.seqrelid = sc.oid
           WHERE c.relnamespace = %s AND NOT c.relname = ANY(%s)
           ORDER BY 1, 2""",
        (ns, ns, excluded),
    )
    constraints = rows(
        conn,
        """SELECT c.relname, k.conname, k.contype::text,
                  ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY u(n, o)
                        JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.n
                        ORDER BY u.o),
                  pg_get_constraintdef(k.oid), k.condeferrable, k.condeferred,
                  k.convalidated, k.connoinherit,
                  t.relname, t.relnamespace = %s, k.confupdtype::text, k.confdeltype::text,
                  k.confmatchtype::text
           FROM pg_constraint k
           JOIN pg_class c ON c.oid = k.conrelid
           LEFT JOIN pg_class t ON t.oid = k.confrelid
           WHERE c.relnamespace = %s AND NOT c.relname = ANY(%s)
           ORDER BY 1, 2""",
        (ns, ns, excluded),
    )
    indexes = rows(
        conn,
        """SELECT t.relname, i.relname, pg_get_indexdef(x.indexrelid), x.indisunique,
                  x.indisprimary, x.indisexclusion, x.indisvalid, x.indisready,
                  ARRAY(SELECT n.nspname || '.' || o.opcname
                        FROM unnest(x.indclass) WITH ORDINALITY u(oc, pos)
                        JOIN pg_opclass o ON o.oid = u.oc
                        JOIN pg_namespace n ON n.oid = o.opcnamespace ORDER BY u.pos),
                  pg_get_expr(x.indpred, x.indrelid), i.relnamespace = %s
           FROM pg_index x
           JOIN pg_class i ON i.oid = x.indexrelid
           JOIN pg_class t ON t.oid = x.indrelid
           WHERE t.relnamespace = %s AND NOT t.relname = ANY(%s)
           ORDER BY 1, 2""",
        (ns, ns, excluded),
    )
    table_options = rows(
        conn,
        """SELECT c.relname, c.reloptions, c.relpersistence::text, c.relrowsecurity,
                  c.relforcerowsecurity, c.relreplident::text, am.amname,
                  ARRAY(SELECT pn.nspname || '.' || pc.relname FROM pg_inherits h
                        JOIN pg_class pc ON pc.oid = h.inhparent
                        JOIN pg_namespace pn ON pn.oid = pc.relnamespace
                        WHERE h.inhrelid = c.oid ORDER BY 1)
           FROM pg_class c LEFT JOIN pg_am am ON am.oid = c.relam
           WHERE c.relnamespace = %s AND c.relkind = 'r' AND NOT c.relname = ANY(%s)
           ORDER BY 1""",
        (ns, excluded),
    )
    # User triggers by definition; internal (foreign-key) triggers have generated names,
    # so only their count and enabled states per table are compared.
    triggers = rows(
        conn,
        """SELECT c.relname, g.tgname, g.tgenabled::text, pg_get_triggerdef(g.oid)
           FROM pg_trigger g JOIN pg_class c ON c.oid = g.tgrelid
           WHERE c.relnamespace = %s AND NOT g.tgisinternal AND NOT c.relname = ANY(%s)
           ORDER BY 1, 2""",
        (ns, excluded),
    )
    internal_triggers = rows(
        conn,
        """SELECT c.relname, count(*), array_agg(g.tgenabled::text ORDER BY g.tgenabled)
           FROM pg_trigger g JOIN pg_class c ON c.oid = g.tgrelid
           WHERE c.relnamespace = %s AND g.tgisinternal AND NOT c.relname = ANY(%s)
           GROUP BY 1 ORDER BY 1""",
        (ns, excluded),
    )
    policies = rows(
        conn,
        """SELECT c.relname, p.polname, p.polcmd::text, p.polpermissive,
                  pg_get_expr(p.polqual, p.polrelid), pg_get_expr(p.polwithcheck, p.polrelid)
           FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
           WHERE c.relnamespace = %s ORDER BY 1, 2""",
        (ns,),
    )
    rules = rows(
        conn,
        """SELECT c.relname, r.rulename, r.ev_type::text, r.is_instead
           FROM pg_rewrite r JOIN pg_class c ON c.oid = r.ev_class
           WHERE c.relnamespace = %s AND r.rulename <> '_RETURN' ORDER BY 1, 2""",
        (ns,),
    )
    statistics = rows(
        conn,
        "SELECT stxname FROM pg_statistic_ext WHERE stxnamespace = %s ORDER BY 1",
        (ns,),
    )
    collations = rows(
        conn,
        f"""SELECT collname FROM pg_collation co
            WHERE co.collnamespace = %s AND {not_extension.format(oid="co.oid")}
            ORDER BY 1""",  # noqa: S608 - fixed fragment only
        (ns,),
    )
    operators = rows(
        conn,
        f"""SELECT oprname || '(' || oprleft::regtype || ',' || oprright::regtype || ')'
            FROM pg_operator o
            WHERE o.oprnamespace = %s AND {not_extension.format(oid="o.oid")}
            ORDER BY 1""",  # noqa: S608 - fixed fragment only
        (ns,),
    )
    return {
        "objects": [list(r) for r in objects],
        "functions": [r[0] for r in functions],
        "types": [r[0] for r in types],
        "tables": tables,
        "columns": [list(r) for r in columns],
        "sequences": [list(r) for r in sequences],
        "constraints": [list(r) for r in constraints],
        # pg_get_indexdef always schema-qualifies the indexed table; only that one
        # qualifier, of the schema under test, is replaced.
        "indexes": [
            [*r[:2], r[2].replace(f" ON {schema}.", " ON <schema>.", 1), *r[3:]] for r in indexes
        ],
        "table_options": [list(r) for r in table_options],
        # pg_get_triggerdef also schema-qualifies the table; the same single rewrite.
        "triggers": [
            [*r[:3], r[3].replace(f" ON {schema}.", " ON <schema>.", 1)] for r in triggers
        ],
        "internal_triggers": [list(r) for r in internal_triggers],
        "policies": [list(r) for r in policies],
        "rules": [list(r) for r in rules],
        "statistics": [r[0] for r in statistics],
        "collations": [r[0] for r in collations],
        "operators": [r[0] for r in operators],
    }


def differences(expected: dict, actual: dict) -> list[str]:
    out = []
    for key in sorted(set(expected) | set(actual)):
        e, a = expected.get(key), actual.get(key)
        if e == a:
            continue
        e_set = {repr(x) for x in e} if isinstance(e, list) else {repr(e)}
        a_set = {repr(x) for x in a} if isinstance(a, list) else {repr(a)}
        for item in sorted(e_set - a_set):
            out.append(f"{key}: missing from migrated schema: {item}")
        for item in sorted(a_set - e_set):
            out.append(f"{key}: unexpected in migrated schema: {item}")
        if e_set == a_set:
            out.append(f"{key}: same items in a different order")
    return out


def extension_inventory(conn) -> list[list[str]]:
    """Every installed extension and its schema (extensions are database-wide)."""
    return [
        list(r)
        for r in rows(
            conn,
            "SELECT e.extname, n.nspname FROM pg_extension e "
            "JOIN pg_namespace n ON n.oid = e.extnamespace ORDER BY 1",
        )
    ]


# plpgsql is installed in every database; the rest must be exactly the required ones.
EXPECTED_EXTENSIONS = sorted(
    [["plpgsql", "pg_catalog"], *([name, "public"] for name in db_schema.REQUIRED_EXTENSIONS)]
)


def non_system_schemas(conn) -> list[str]:
    return [
        r[0]
        for r in rows(
            conn,
            "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg\\_%' "
            "AND nspname <> 'information_schema' ORDER BY 1",
        )
    ]


def require_only_public(conn) -> None:
    found = non_system_schemas(conn)
    if found != ["public"]:
        raise UnsafeDatabaseState(f"Unexpected schemas before the drift check: {found}")


def materialize_expected(conn, metadata, scratch: str) -> None:
    """
    Creates the metadata's tables in the new schema `scratch`, inside the caller's
    transaction, which the caller must roll back. Tables with an explicit schema are
    skipped. Raises if `scratch` already exists.
    """
    conn.execute(f'CREATE SCHEMA "{scratch}"')
    conn.execute("SELECT set_config('search_path', %s, true)", (f'"{scratch}", public',))
    dialect = pg_dialect.dialect()
    for table in metadata.sorted_tables:
        if table.schema is not None:
            continue
        conn.execute(str(CreateTable(table).compile(dialect=dialect)))
        for index in sorted(table.indexes, key=lambda i: i.name):
            conn.execute(str(CreateIndex(index).compile(dialect=dialect)))


# --- migration state -------------------------------------------------------------------

FRESH = "fresh"
ALEMBIC_BASELINE_STATE = "alembic-baseline"
ELIGIBLE = "eligible-for-future-adoption-not-adopted"
INVALID_LEDGER = "invalid-historical-ledger"
SCHEMA_DRIFT = "schema-drift"
UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class MigrationState:
    """
    What a read-only inspection found. ELIGIBLE means only that a future, separately
    authorized adoption step could consider this database; nothing has been adopted,
    stamped or changed.
    """

    state: str
    reasons: tuple[str, ...]


def _table_columns(conn, table) -> list[tuple]:
    return rows(
        conn,
        """SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull
           FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
           WHERE c.relnamespace = 'public'::regnamespace AND c.relname = %s
             AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum""",
        (table,),
    )


LEDGER_COLUMNS = [("filename", "text", True), ("applied_at", "timestamp with time zone", True)]
ALEMBIC_COLUMNS = [("version_num", "character varying(32)", True)]


def public_relations(conn) -> set[str]:
    """Names of all tables, views and foreign tables in public (sequences and indexes excluded)."""
    return {
        r[0]
        for r in rows(
            conn,
            "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
            "AND relkind IN ('r', 'p', 'v', 'm', 'f')",
        )
    }


def environment_problems(conn) -> list[str]:
    """Objects outside public, or extensions other than the expected ones."""
    problems = []
    if non_system_schemas(conn) != ["public"]:
        problems.append(f"unexpected schemas: {non_system_schemas(conn)}")
    if extension_inventory(conn) != EXPECTED_EXTENSIONS:
        problems.append(f"unexpected extensions: {extension_inventory(conn)}")
    return problems


VERIFY_SCRATCH = "medibridge_expected_schema"


def alembic_table_problems(conn) -> list[str]:
    """alembic_version must have exactly Alembic's column, primary key and nothing else."""
    problems = []
    if _table_columns(conn, ALEMBIC_TABLE) != ALEMBIC_COLUMNS:
        problems.append("alembic_version has unexpected columns")
    extra = rows(
        conn,
        """SELECT count(*) FROM pg_trigger WHERE tgrelid = 'public.alembic_version'::regclass
           AND NOT tgisinternal""",
    )[0][0]
    indexes = [
        r[0]
        for r in rows(
            conn,
            "SELECT indexrelid::regclass::text FROM pg_index "
            "WHERE indrelid = 'public.alembic_version'::regclass ORDER BY 1",
        )
    ]
    if extra or indexes != ["alembic_version_pkc"]:
        problems.append("alembic_version has unexpected triggers or indexes")
    return problems


def verify_against_metadata(conn, metadata) -> list[str]:
    """
    Differences between public (without alembic_version) and `metadata`, inside the
    caller's open transaction. The expected schema is created under a savepoint that is
    always rolled back, so nothing it creates survives, whatever happens. Needs CREATE on
    the database (to create the scratch schema). Leaves search_path changed; callers that
    rely on it must set it again.
    """
    # A savepoint that psycopg always rolls back, also when materialization fails; an
    # error from inside propagates unchanged.
    with conn.transaction(force_rollback=True):
        materialize_expected(conn, metadata, VERIFY_SCRATCH)
        expected = snapshot(conn, VERIFY_SCRATCH)
    found = differences(expected, snapshot(conn, "public", (ALEMBIC_TABLE,)))
    return found + environment_problems(conn)


def is_empty(conn) -> bool:
    """
    True only for a database with nothing in it: no schema but public, no extension but
    plpgsql, and no object of any compared kind in public.
    """
    if non_system_schemas(conn) != ["public"]:
        return False
    if extension_inventory(conn) != [["plpgsql", "pg_catalog"]]:
        return False
    return not any(snapshot(conn, "public").values())


def inspect_migration_state(conn, *, database_name: str, expected: dict) -> MigrationState:
    """
    Classifies the public schema of the connected database. Read-only: it runs SELECTs
    only (run it in a READ ONLY transaction). `expected` is a snapshot of the schema the
    metadata describes, as materialize_expected() and snapshot() produce it.
    """
    connected = rows(conn, "SELECT current_database()")[0][0]
    if connected != database_name:
        raise UnsafeDatabaseState("The connection is not attached to the expected database.")

    present = public_relations(conn)
    has_ledger, has_alembic = LEDGER_TABLE in present, ALEMBIC_TABLE in present
    application = set(db_schema.metadata.tables)
    reasons = []

    if is_empty(conn):
        return MigrationState(FRESH, ())
    if not present:
        return MigrationState(
            UNSUPPORTED, ("objects outside public tables, or extensions, without any ledger",)
        )
    problems = environment_problems(conn)
    if has_ledger and has_alembic:
        return MigrationState(
            UNSUPPORTED, ("both the historical ledger and alembic_version exist",)
        )

    if has_alembic:
        if _table_columns(conn, ALEMBIC_TABLE) != ALEMBIC_COLUMNS:
            return MigrationState(UNSUPPORTED, ("alembic_version has an unexpected structure",))
        versions = [r[0] for r in rows(conn, "SELECT version_num FROM alembic_version")]
        if versions != [ALEMBIC_BASELINE]:
            return MigrationState(UNSUPPORTED, (f"unexpected Alembic versions {sorted(versions)}",))
        if problems:
            return MigrationState(UNSUPPORTED, tuple(problems))
        found = differences(expected, snapshot(conn, "public", (ALEMBIC_TABLE,)))
        state = ALEMBIC_BASELINE_STATE if not found else SCHEMA_DRIFT
        return MigrationState(state, tuple(found))

    if not has_ledger:
        partial = sorted(present & application)
        return MigrationState(
            UNSUPPORTED,
            (f"application objects without any migration ledger: {partial or sorted(present)}",),
        )

    if _table_columns(conn, LEDGER_TABLE) != LEDGER_COLUMNS:
        return MigrationState(INVALID_LEDGER, ("schema_migrations has an unexpected structure",))
    recorded = [
        r[0] for r in rows(conn, "SELECT filename FROM schema_migrations ORDER BY filename")
    ]
    if recorded != sorted(HISTORICAL_MIGRATIONS):
        missing = sorted(set(HISTORICAL_MIGRATIONS) - set(recorded))
        extra = sorted(set(recorded) - set(HISTORICAL_MIGRATIONS))
        reasons.append(f"ledger mismatch: missing {missing}, unexpected {extra}")
        return MigrationState(INVALID_LEDGER, tuple(reasons))

    if problems:
        return MigrationState(UNSUPPORTED, tuple(problems))
    found = differences(expected, snapshot(conn, "public", (LEDGER_TABLE,)))
    if found:
        return MigrationState(SCHEMA_DRIFT, tuple(found))
    return MigrationState(ELIGIBLE, ())
