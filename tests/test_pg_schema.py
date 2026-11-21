"""
Migrations and schema invariants, checked in this session's disposable database.

The schema here was created by `migrate.apply_migrations()` when the session
started. These tests read PostgreSQL's catalogs and assert the structure the
application relies on, using catalog fields rather than whole definition
strings where practical. Generated OIDs are never compared.
"""

import re
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

import migrate
from tests import db_safety

MIGRATIONS = sorted(
    p.name for p in (Path(__file__).resolve().parents[1] / "migrations").glob("*.sql")
)


@pytest.fixture
def db(disposable_database):
    opened = []

    def connect(autocommit=True):
        conn = psycopg.connect(
            **disposable_database.config.connect_kwargs(disposable_database.name),
            autocommit=autocommit,
        )
        opened.append(conn)
        found, role_oid = db_safety.fetch_identity(conn, disposable_database.name)
        db_safety.check_identity(
            session_name=disposable_database.name,
            created=disposable_database.created,
            found=found,
            current_role_oid=role_oid,
            require_marker=True,
        )
        if not autocommit:
            conn.commit()
        return conn

    yield connect
    for conn in opened:
        if not conn.closed:
            conn.close()


def constraint_columns(conn, name):
    return conn.execute(
        """
        SELECT array_agg(a.attname ORDER BY k.ord)
        FROM pg_constraint c
        CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
        WHERE c.conname = %s AND c.connamespace = 'public'::regnamespace
        """,
        (name,),
    ).fetchone()[0]


def constraint_definition(conn, name):
    return conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = %s AND connamespace = 'public'::regnamespace",
        (name,),
    ).fetchone()[0]


def schema_snapshot(conn):
    constraints = conn.execute(
        "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2"
    ).fetchall()
    indexes = conn.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1"
    ).fetchall()
    return constraints, indexes


# --- migrations ------------------------------------------------------------------------


def test_migration_ledger_lists_every_migration_file_once(db):
    rows = db().execute("SELECT filename FROM schema_migrations ORDER BY filename").fetchall()

    assert [r[0] for r in rows] == MIGRATIONS
    assert len(MIGRATIONS) == 8
    assert MIGRATIONS[0].startswith("000_")


def test_rerunning_migrations_applies_nothing_and_keeps_schema_and_data(db):
    conn = db()
    ledger = conn.execute(
        "SELECT filename, applied_at FROM schema_migrations ORDER BY filename"
    ).fetchall()
    schema = schema_snapshot(conn)
    patient_id = conn.execute(
        "INSERT INTO patients (first_name, last_name, date_of_birth, gender) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        ("Sky", "Migration", "1988-11-30", "other"),
    ).fetchone()[0]

    migrate.apply_migrations()  # the project's own entry point, against this database

    after = db()
    assert (
        after.execute(
            "SELECT filename, applied_at FROM schema_migrations ORDER BY filename"
        ).fetchall()
    ) == ledger
    assert schema_snapshot(after) == schema
    row = after.execute("SELECT first_name, last_name FROM patients WHERE id = %s", (patient_id,))
    assert row.fetchone() == ("Sky", "Migration")


# --- schema ---------------------------------------------------------------------------


def test_application_tables_and_extension_exist(db):
    conn = db()
    tables = conn.execute(
        "SELECT array_agg(tablename ORDER BY tablename) FROM pg_tables WHERE schemaname = 'public'"
    ).fetchone()[0]
    extensions = {r[0] for r in conn.execute("SELECT extname FROM pg_extension").fetchall()}

    assert tables == [
        "appointments",
        "audit_logs",
        "hl7_messages",
        "patient_identifiers",
        "patients",
        "providers",
        "schema_migrations",
        "users",
    ]
    assert "btree_gist" in extensions


def test_appointment_overlap_exclusion_constraint(db):
    """Provider-specific, half-open ranges, scheduled rows only, enforced with GiST."""
    conn = db()
    contype, method, operators = conn.execute(
        """
        SELECT c.contype, am.amname,
               (SELECT array_agg(o.oprname ORDER BY u.ord)
                FROM unnest(c.conexclop) WITH ORDINALITY AS u(op, ord)
                JOIN pg_operator o ON o.oid = u.op)
        FROM pg_constraint c
        JOIN pg_class i ON i.oid = c.conindid
        JOIN pg_am am ON am.oid = i.relam
        WHERE c.conname = 'appointments_no_provider_overlap'
        """
    ).fetchone()
    columns, predicate = conn.execute(
        """
        SELECT ARRAY[pg_get_indexdef(x.indexrelid, 1, true),
                     pg_get_indexdef(x.indexrelid, 2, true)],
               pg_get_expr(x.indpred, x.indrelid)
        FROM pg_index x
        JOIN pg_class i ON i.oid = x.indexrelid
        WHERE i.relname = 'appointments_no_provider_overlap'
        """
    ).fetchone()

    assert (contype, method, operators) == ("x", "gist", ["=", "&&"])
    assert columns[0] == "provider_id"
    range_pattern = r"tstzrange\(starts_at, ends_at, '\[\)'::text\)"
    assert re.fullmatch(range_pattern, columns[1]), f"unexpected range column: {columns[1]}"
    assert predicate is not None, "the exclusion constraint has no WHERE predicate"
    scheduled_only = r"\(+status\)::text = 'scheduled'::text\)*"
    assert re.fullmatch(scheduled_only, predicate), f"unexpected predicate: {predicate}"


def test_check_constraints(db):
    conn = db()
    assert re.search(r"ends_at > starts_at", constraint_definition(conn, "appointments_valid_time"))
    roles = set(re.findall(r"'(\w+)'", constraint_definition(conn, "users_valid_role")))
    assert roles == {"admin", "clinician", "scheduler", "viewer"}
    statuses = set(re.findall(r"'(\w+)'", constraint_definition(conn, "appointments_valid_status")))
    assert statuses == {"scheduled", "completed", "cancelled"}


@pytest.mark.parametrize(
    ("name", "table", "columns", "references", "on_delete"),
    [
        ("appointments_patient_id_fkey", "appointments", ["patient_id"], "patients", "r"),
        ("appointments_provider_id_fkey", "appointments", ["provider_id"], "providers", "r"),
        ("fk_patient_identifiers_patient", "patient_identifiers", ["patient_id"], "patients", "c"),
        ("audit_logs_user_id_fkey", "audit_logs", ["user_id"], "users", "n"),
    ],
    ids=["appointment-patient", "appointment-provider", "identifier-patient", "audit-user"],
)
def test_foreign_keys_and_delete_actions(db, name, table, columns, references, on_delete):
    """Delete actions: r = RESTRICT, c = CASCADE, n = SET NULL."""
    conn = db()
    row = conn.execute(
        "SELECT conrelid::regclass::text, confrelid::regclass::text, confdeltype "
        "FROM pg_constraint WHERE conname = %s AND contype = 'f'",
        (name,),
    ).fetchone()

    assert row == (table, references, on_delete)
    assert constraint_columns(conn, name) == columns


@pytest.mark.parametrize(
    ("name", "columns"),
    [
        ("unique_patient_identifier", ["system", "value"]),
        ("providers_email_key", ["email"]),
        ("users_username_key", ["username"]),
        ("users_email_key", ["email"]),
        (
            "uq_hl7_message_source_control",
            ["sending_application", "sending_facility", "message_control_id"],
        ),
    ],
)
def test_unique_constraints(db, name, columns):
    conn = db()
    contype = conn.execute("SELECT contype FROM pg_constraint WHERE conname = %s", (name,))

    assert contype.fetchone()[0] == "u"
    assert constraint_columns(conn, name) == columns


def test_migration_indexes_exist_on_the_expected_columns(db):
    rows = (
        db()
        .execute(
            """
        SELECT i.relname, array_agg(a.attname ORDER BY k.ord)
        FROM pg_index x
        JOIN pg_class i ON i.oid = x.indexrelid
        CROSS JOIN LATERAL unnest(x.indkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.attnum
        WHERE i.relname IN ('idx_appointments_patient_id', 'idx_appointments_provider_id',
                            'idx_appointments_starts_at', 'idx_users_role')
        GROUP BY i.relname
        """
        )
        .fetchall()
    )

    assert dict(rows) == {
        "idx_appointments_patient_id": ["patient_id"],
        "idx_appointments_provider_id": ["provider_id"],
        "idx_appointments_starts_at": ["starts_at"],
        "idx_users_role": ["role"],
    }


def test_database_rejects_an_appointment_that_ends_before_it_starts(db):
    """The API rejects this first (422), so the CHECK is exercised with direct SQL."""
    setup = db()
    patient_id = setup.execute(
        "INSERT INTO patients (first_name, last_name, date_of_birth, gender) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        ("Drew", "Timecheck", "1970-06-01", "unknown"),
    ).fetchone()[0]
    provider_id = setup.execute(
        "INSERT INTO providers (first_name, last_name, specialty) VALUES (%s, %s, %s) RETURNING id",
        ("Lee", "Timecheck", "Cardiology"),
    ).fetchone()[0]
    conn = db(autocommit=False)
    insert = (
        "INSERT INTO appointments (patient_id, provider_id, starts_at, ends_at) "
        "VALUES (%s, %s, %s, %s)"
    )

    with pytest.raises(errors.CheckViolation) as raised:
        conn.execute(insert, (patient_id, provider_id, "2031-06-01T10:00Z", "2031-06-01T09:00Z"))
    assert raised.value.sqlstate == "23514"
    assert raised.value.diag.constraint_name == "appointments_valid_time"

    conn.rollback()  # the failed transaction is recoverable on the same connection
    conn.execute(insert, (patient_id, provider_id, "2031-06-01T09:00Z", "2031-06-01T10:00Z"))
    conn.commit()
    assert db().execute("SELECT count(*) FROM appointments").fetchone()[0] == 1
