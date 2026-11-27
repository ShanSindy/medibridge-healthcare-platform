"""
Schema drift check: db_schema.metadata against the schema the migrations build.

The migrated schema is `public` in this session's disposable database. The expected
schema is built from the metadata in a scratch schema, inside one transaction that is
always rolled back, so nothing outside `public` survives a test (the harness refuses
databases with tables in other schemas).

Both schemas are read the same way from the PostgreSQL catalogs. Definitions come from
PostgreSQL's own deparsers (pg_get_constraintdef, pg_get_indexdef, pg_get_expr,
format_type) with search_path set to "<schema under test>, public": objects in the
schema under test print unqualified, the btree_gist objects in public print unqualified
in both snapshots, and any other cross-schema reference prints schema-qualified and so
shows up as a difference. Foreign-key targets, index tables and identity sequences are
also checked structurally to lie in the schema under test.

Compared: the inventory of relations, functions, types, collations and operators in the
schema (extension members excluded); per column the ordinal position, name, type,
collation, nullability, default, identity mode and generated status; identity sequence
settings; every constraint's type, columns, definition, deferrability, validation, NO
INHERIT flag and foreign-key target, actions and match type; every index's definition,
uniqueness, validity and operator classes; table options, persistence, row-level
security, replica identity, access method and inheritance; triggers (user triggers by
definition, internal ones by count and enabled state); policies; rules; extended
statistics; and, database-wide, the installed extensions with their schemas.

Not compared, deliberately: the migration ledger table schema_migrations (created by
migrate.py, not by a migration) and its index; OIDs, owners, privileges and tablespaces;
planner statistics, storage and comments; extension versions; current sequence values;
internal trigger names. See docs/decisions/0002-schema-metadata-and-drift-check.md.
"""

import ast
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import (
    CheckConstraint,
    Column,
    DefaultClause,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
    text,
)
from sqlalchemy.dialects.postgresql import psycopg as pg_dialect
from sqlalchemy.schema import CreateIndex, CreateTable

import db_schema
from tests import db_safety
from tests.pg_helpers import verified_connection

SCRATCH = "schema_expected"
EXCLUDED_TABLES = {"public": {"schema_migrations"}}
ROOT = Path(__file__).resolve().parents[1]


# --- catalog snapshot ----------------------------------------------------------------------


def rows(conn, query, params=None):
    return conn.execute(query, params).fetchall()


def snapshot(conn, schema: str) -> dict:
    """Every compared property of `schema`, as plain data. Run inside a transaction."""
    conn.execute("SELECT set_config('search_path', %s, true)", (f'"{schema}", public',))
    excluded = sorted(EXCLUDED_TABLES.get(schema, ()))
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


# --- the expected schema, built from metadata inside a rolled-back transaction ------------


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
        raise db_safety.UnsafeTestDatabase(f"Unexpected schemas before the drift check: {found}")


@contextmanager
def drift_transaction(disposable_database):
    """
    One bounded transaction on this session's verified database, always rolled back.
    Refuses to start if any schema other than public already exists.
    """
    with verified_connection(disposable_database) as conn:
        conn.autocommit = False
        try:
            if conn.execute("SELECT current_database()").fetchone()[0] != disposable_database.name:
                raise db_safety.UnsafeTestDatabase("Not this session's test database.")
            require_only_public(conn)
            conn.execute("SET LOCAL lock_timeout = '5s'")
            conn.execute("SET LOCAL statement_timeout = '60s'")
            yield conn
        finally:
            conn.rollback()


def build_expected(conn, metadata) -> None:
    """Creates the metadata's tables in SCRATCH; tables with an explicit schema are skipped."""
    conn.execute(f'CREATE SCHEMA "{SCRATCH}"')
    conn.execute("SELECT set_config('search_path', %s, true)", (f'"{SCRATCH}", public',))
    dialect = pg_dialect.dialect()
    for table in metadata.sorted_tables:
        if table.schema is not None:
            continue
        conn.execute(str(CreateTable(table).compile(dialect=dialect)))
        for index in sorted(table.indexes, key=lambda i: i.name):
            conn.execute(str(CreateIndex(index).compile(dialect=dialect)))


def drift(disposable_database, metadata=None, actual_changes=()) -> list[str]:
    """Differences between `metadata` and the migrated schema (after `actual_changes`)."""
    with drift_transaction(disposable_database) as conn:
        for statement in actual_changes:
            conn.execute(statement)
        actual = {**snapshot(conn, "public"), "extensions": extension_inventory(conn)}
        build_expected(conn, metadata if metadata is not None else db_schema.metadata)
        expected = {**snapshot(conn, SCRATCH), "extensions": EXPECTED_EXTENSIONS}
        return differences(expected, actual)


# --- the check -----------------------------------------------------------------------------


def test_metadata_matches_the_migrated_schema(disposable_database):
    assert drift(disposable_database) == []


def test_the_snapshot_covers_the_whole_schema(disposable_database):
    with drift_transaction(disposable_database) as conn:
        snap = snapshot(conn, "public")
        extensions = extension_inventory(conn)
    assert snap["tables"] == sorted(db_schema.metadata.tables)
    assert extensions == EXPECTED_EXTENSIONS
    for section in ("triggers", "policies", "rules", "statistics", "collations", "operators"):
        assert snap[section] == [], section
    assert len(snap["columns"]) == sum(len(t.columns) for t in db_schema.metadata.tables.values())
    assert {kind for kind, _ in snap["objects"]} == {"r", "i", "S"}  # nothing else in public
    assert all(row[-1] for row in snap["sequences"])  # every identity sequence in public
    assert "schema_migrations" not in str(snap)  # the only exclusion, with its index


def test_the_expected_schema_never_outlives_its_transaction(disposable_database):
    assert drift(disposable_database) == []
    with verified_connection(disposable_database) as conn:
        assert non_system_schemas(conn) == ["public"]
    disposable_database.clean()  # the harness still accepts the database


def test_drift_transaction_refuses_to_start_beside_another_schema(disposable_database, monkeypatch):

    monkeypatch.setattr(
        sys.modules[__name__], "non_system_schemas", lambda conn: ["other", "public"]
    )
    with pytest.raises(db_safety.UnsafeTestDatabase, match="Unexpected schemas"):
        with drift_transaction(disposable_database):
            pytest.fail("the check must not start")


def test_an_unexpected_existing_schema_stops_the_check(disposable_database):
    with verified_connection(disposable_database) as conn:
        conn.autocommit = False
        try:
            conn.execute('CREATE SCHEMA "unexpected_existing"')
            with pytest.raises(db_safety.UnsafeTestDatabase, match="Unexpected schemas"):
                require_only_public(conn)
        finally:
            conn.rollback()
    with verified_connection(disposable_database) as conn:
        assert non_system_schemas(conn) == ["public"]


def test_the_expected_schema_needs_no_superuser_statement(disposable_database):
    with verified_connection(disposable_database) as conn:
        can_create = conn.execute(
            "SELECT has_database_privilege(current_user, current_database(), 'CREATE')"
        ).fetchone()[0]
    assert can_create  # CREATE SCHEMA needs only this, which the database owner has
    dialect = pg_dialect.dialect()
    ddl = " ".join(
        [str(CreateTable(t).compile(dialect=dialect)) for t in db_schema.metadata.sorted_tables]
        + [
            str(CreateIndex(i).compile(dialect=dialect))
            for t in db_schema.metadata.sorted_tables
            for i in t.indexes
        ]
    )
    assert "EXTENSION" not in ddl.upper()
    assert " ROLE " not in ddl.upper()


# --- drift in the metadata is detected ---------------------------------------------------


def _column(metadata, table, name):
    return metadata.tables[table].c[name]


def _constraint(metadata, table, name):
    return next(c for c in metadata.tables[table].constraints if c.name == name)


def _replace_check(metadata, table, name, sqltext):
    t = metadata.tables[table]
    t.constraints.discard(_constraint(metadata, table, name))
    t.append_constraint(CheckConstraint(sqltext, name=name))


def _replace_fk(metadata, table, name, **changes):
    t = metadata.tables[table]
    old = _constraint(metadata, table, name)
    spec = {
        "columns": [c.name for c in old.columns],
        "refcolumns": [e.target_fullname for e in old.elements],
        "name": name,
        "ondelete": old.ondelete,
    } | changes
    t.constraints.discard(old)
    for column in old.columns:
        column.foreign_keys.difference_update(old.elements)
    for element in old.elements:
        t.foreign_keys.discard(element)
    t.append_constraint(ForeignKeyConstraint(**spec))


def _default(metadata, table, column, sql):
    _column(metadata, table, column).server_default = DefaultClause(text(sql))


def _cross_schema_fk(metadata):
    Table("patients", metadata, Column("id", Integer, primary_key=True), schema="public")
    _replace_fk(
        metadata,
        "patient_identifiers",
        "fk_patient_identifiers_patient",
        refcolumns=["public.patients.id"],
    )


def _drop_index(metadata, table, name):
    t = metadata.tables[table]
    t.indexes.discard(next(i for i in t.indexes if i.name == name))


METADATA_DRIFT = {
    "type length": lambda m: setattr(_column(m, "patients", "first_name").type, "length", 120),
    "nullability": lambda m: setattr(_column(m, "providers", "phone"), "nullable", False),
    "NOW() vs CURRENT_TIMESTAMP": lambda m: _default(
        m, "audit_logs", "created_at", "CURRENT_TIMESTAMP"
    ),
    "default value": lambda m: _default(m, "users", "role", "'clinician'"),
    "identity mode": lambda m: setattr(_column(m, "patients", "id").identity, "always", False),
    "identity start": lambda m: setattr(_column(m, "users", "id").identity, "start", 10),
    "missing index": lambda m: _drop_index(m, "users", "idx_users_role"),
    "check text": lambda m: _replace_check(
        m, "users", "users_valid_role", "role IN ('admin', 'clinician', 'scheduler')"
    ),
    "foreign key action": lambda m: _replace_fk(
        m, "patient_identifiers", "fk_patient_identifiers_patient", ondelete="RESTRICT"
    ),
    "deferrable foreign key": lambda m: _replace_fk(
        m, "appointments", "appointments_patient_id_fkey", deferrable=True
    ),
    "exclusion predicate": lambda m: setattr(
        _constraint(m, "appointments", "appointments_no_provider_overlap"), "where", None
    ),
    "constraint name": lambda m: setattr(
        _constraint(m, "providers", "providers_email_key"), "name", "providers_email_unique"
    ),
    "extra column": lambda m: m.tables["patients"].append_column(Column("nickname", String(50))),
    "missing table": lambda m: m.remove(m.tables["patient_identifiers"]),
    "cross-schema reference": _cross_schema_fk,
}


@pytest.mark.parametrize("name", METADATA_DRIFT)
def test_metadata_drift_is_detected(disposable_database, name):
    metadata = db_schema.build_metadata()
    METADATA_DRIFT[name](metadata)

    found = drift(disposable_database, metadata)

    assert EXPECTED_SECTION[name] in sections(found), found
    assert drift(disposable_database) == []  # the shared metadata is untouched


# --- drift in the migrated schema is detected (each change rolled back) ------------------

ACTUAL_DRIFT = {
    "column type": ["ALTER TABLE providers ALTER COLUMN phone TYPE varchar(40)"],
    "dropped default": ["ALTER TABLE users ALTER COLUMN role DROP DEFAULT"],
    "dropped index": ["DROP INDEX idx_users_role"],
    "extra index": ["CREATE INDEX extra_idx ON patients (last_name)"],
    "unvalidated constraint": [
        "ALTER TABLE users DROP CONSTRAINT users_valid_role",
        "ALTER TABLE users ADD CONSTRAINT users_valid_role "
        "CHECK (role IN ('admin', 'clinician', 'scheduler', 'viewer')) NOT VALID",
    ],
    "unexpected trigger": [
        "CREATE TRIGGER extra_trigger BEFORE UPDATE ON patients FOR EACH ROW "
        "EXECUTE FUNCTION suppress_redundant_updates_trigger()"
    ],
    "unlogged table": ["ALTER TABLE hl7_messages SET UNLOGGED"],
    "row level security": ["ALTER TABLE patients ENABLE ROW LEVEL SECURITY"],
    "replica identity": ["ALTER TABLE patients REPLICA IDENTITY FULL"],
    "unexpected policy": ["CREATE POLICY extra_policy ON patients USING (true)"],
    "unexpected rule": ["CREATE RULE extra_rule AS ON INSERT TO hl7_messages DO INSTEAD NOTHING"],
    "extended statistics": ["CREATE STATISTICS extra_stats ON first_name, last_name FROM patients"],
    "unexpected extension": ["CREATE EXTENSION pg_trgm"],
    "unexpected table": ["CREATE TABLE extra_table (id integer)"],
    "unexpected view": ["CREATE VIEW extra_view AS SELECT 1 AS one"],
    "unexpected function": [
        "CREATE FUNCTION extra_function() RETURNS integer LANGUAGE sql AS 'SELECT 1'"
    ],
    "unexpected sequence": ["CREATE SEQUENCE extra_sequence"],
    "cross-schema foreign key": [
        "CREATE SCHEMA drift_other",
        "CREATE TABLE drift_other.patients (id integer PRIMARY KEY)",
        "ALTER TABLE patient_identifiers DROP CONSTRAINT fk_patient_identifiers_patient",
        "ALTER TABLE patient_identifiers ADD CONSTRAINT fk_patient_identifiers_patient "
        "FOREIGN KEY (patient_id) REFERENCES drift_other.patients(id) ON DELETE CASCADE",
    ],
}


@pytest.mark.parametrize("name", ACTUAL_DRIFT)
def test_drift_in_the_migrated_schema_is_detected(disposable_database, name):
    found = drift(disposable_database, actual_changes=ACTUAL_DRIFT[name])
    assert EXPECTED_SECTION[name] in sections(found), found
    with verified_connection(disposable_database) as conn:  # every change was rolled back
        assert non_system_schemas(conn) == ["public"]
    assert drift(disposable_database) == []


def test_a_dropped_and_re_added_column_is_not_drift(disposable_database):
    assert (
        drift(
            disposable_database,
            actual_changes=[
                "ALTER TABLE providers DROP COLUMN created_at",
                "ALTER TABLE providers ADD COLUMN created_at TIMESTAMPTZ NOT NULL "
                "DEFAULT CURRENT_TIMESTAMP",
            ],
        )
        == []
    )


# The catalog section in which each change must show up.
EXPECTED_SECTION = {
    "type length": "columns",
    "nullability": "columns",
    "NOW() vs CURRENT_TIMESTAMP": "columns",
    "default value": "columns",
    "identity mode": "columns",
    "identity start": "sequences",
    "missing index": "indexes",
    "check text": "constraints",
    "foreign key action": "constraints",
    "deferrable foreign key": "constraints",
    "exclusion predicate": "constraints",
    "constraint name": "constraints",
    "extra column": "columns",
    "missing table": "tables",
    "cross-schema reference": "constraints",
    "column type": "columns",
    "dropped default": "columns",
    "dropped index": "indexes",
    "extra index": "indexes",
    "unvalidated constraint": "constraints",
    "unexpected trigger": "triggers",
    "unlogged table": "table_options",
    "row level security": "table_options",
    "replica identity": "table_options",
    "unexpected policy": "policies",
    "unexpected rule": "rules",
    "extended statistics": "statistics",
    "unexpected extension": "extensions",
    "unexpected table": "tables",
    "unexpected view": "objects",
    "unexpected function": "functions",
    "unexpected sequence": "objects",
    "cross-schema foreign key": "constraints",
}


def sections(found):
    return {line.split(":", 1)[0] for line in found}


# --- no runtime use ------------------------------------------------------------------------


def test_metadata_imports_without_an_engine_or_connection():
    code = (
        "import psycopg, sqlalchemy\n"
        "def forbidden(*a, **k): raise SystemExit('connection attempted')\n"
        "psycopg.connect = forbidden\n"
        "sqlalchemy.create_engine = forbidden\n"
        "import db_schema, sys\n"
        "assert 'db_engine' not in sys.modules and 'database' not in sys.modules\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed arguments, no untrusted input
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


def test_no_application_module_imports_the_metadata():
    importers = []
    for path in [*ROOT.glob("*.py"), *ROOT.glob("routers/*.py")]:
        if path.name in {"db_schema.py", "conftest.py"}:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module == "db_schema":
                importers.append(path.name)
            if isinstance(node, ast.Import) and any(a.name == "db_schema" for a in node.names):
                importers.append(path.name)
    assert importers == []
