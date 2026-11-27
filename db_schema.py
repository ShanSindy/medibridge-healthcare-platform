"""
The application schema as SQLAlchemy Core metadata: descriptive only.

The SQL migrations in migrations/ (applied by migrate.py) remain the source of truth
and the only thing that changes a database. Nothing at runtime imports this module or
creates tables from it. tests/test_schema_drift.py checks that this metadata and the
migrated schema are identical, so it can later serve as the target metadata for Alembic.

Every constraint and index is named exactly as the migrations name it, or as PostgreSQL
named it when the migration did not (for example providers_email_key). Server defaults
are written as the migrations wrote them: PostgreSQL stores NOW() and CURRENT_TIMESTAMP
differently, and both occur. The runtime queries keep their own untyped table()
definitions (see the services), which avoid typed bind casts.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, ExcludeConstraint

# Extensions the schema depends on (migration 005).
REQUIRED_EXTENSIONS = frozenset({"btree_gist"})

TIMESTAMPTZ = TIMESTAMP(timezone=True)


def _id(kind=Integer):
    return Column("id", kind, Identity(always=True), nullable=False)


def build_metadata() -> MetaData:
    """A fresh copy of the schema metadata (tests change copies, never the shared one)."""
    metadata = MetaData()

    Table(  # 000
        "patients",
        metadata,
        _id(),
        Column("first_name", String(100), nullable=False),
        Column("last_name", String(100), nullable=False),
        Column("date_of_birth", Date, nullable=False),
        Column("gender", String(20), nullable=False),
        PrimaryKeyConstraint("id", name="patients_pkey"),
    )
    Table(  # 001
        "patient_identifiers",
        metadata,
        _id(),
        Column("patient_id", Integer, nullable=False),
        Column("system", String(255), nullable=False),
        Column("value", String(100), nullable=False),
        PrimaryKeyConstraint("id", name="patient_identifiers_pkey"),
        ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name="fk_patient_identifiers_patient",
            ondelete="CASCADE",
        ),
        UniqueConstraint("system", "value", name="unique_patient_identifier"),
    )
    Table(  # 002
        "hl7_messages",
        metadata,
        _id(BigInteger),
        Column("sending_application", Text, nullable=False),
        Column("sending_facility", Text, nullable=False),
        Column("message_control_id", Text, nullable=False),
        Column("message_type", Text, nullable=False),
        Column("status", Text, nullable=False, server_default=text("'processed'")),
        Column(
            "received_at", TIMESTAMPTZ, nullable=False, server_default=text("CURRENT_TIMESTAMP")
        ),
        PrimaryKeyConstraint("id", name="hl7_messages_pkey"),
        UniqueConstraint(
            "sending_application",
            "sending_facility",
            "message_control_id",
            name="uq_hl7_message_source_control",
        ),
        CheckConstraint("status IN ('processed', 'failed')", name="chk_hl7_message_status"),
    )
    Table(  # 003
        "providers",
        metadata,
        _id(),
        Column("first_name", String(100), nullable=False),
        Column("last_name", String(100), nullable=False),
        Column("specialty", String(150), nullable=False),
        Column("email", String(255)),
        Column("phone", String(30)),
        Column("created_at", TIMESTAMPTZ, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
        PrimaryKeyConstraint("id", name="providers_pkey"),
        UniqueConstraint("email", name="providers_email_key"),
        CheckConstraint("LENGTH(TRIM(first_name)) > 0", name="providers_first_name_not_blank"),
        CheckConstraint("LENGTH(TRIM(last_name)) > 0", name="providers_last_name_not_blank"),
        CheckConstraint("LENGTH(TRIM(specialty)) > 0", name="providers_specialty_not_blank"),
    )
    appointments = Table(  # 004, 005
        "appointments",
        metadata,
        _id(),
        Column("patient_id", Integer, nullable=False),
        Column("provider_id", Integer, nullable=False),
        Column("starts_at", TIMESTAMPTZ, nullable=False),
        Column("ends_at", TIMESTAMPTZ, nullable=False),
        Column("status", String(20), nullable=False, server_default=text("'scheduled'")),
        Column("reason", String(500)),
        Column("created_at", TIMESTAMPTZ, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
        PrimaryKeyConstraint("id", name="appointments_pkey"),
        ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name="appointments_patient_id_fkey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
            name="appointments_provider_id_fkey",
            ondelete="RESTRICT",
        ),
        CheckConstraint("ends_at > starts_at", name="appointments_valid_time"),
        CheckConstraint(
            "status IN ('scheduled', 'completed', 'cancelled')", name="appointments_valid_status"
        ),
        Index("idx_appointments_patient_id", "patient_id"),
        Index("idx_appointments_provider_id", "provider_id"),
        Index("idx_appointments_starts_at", "starts_at"),
    )
    c = appointments.c
    appointments.append_constraint(
        ExcludeConstraint(
            (c.provider_id, "="),
            (func.tstzrange(c.starts_at, c.ends_at, literal_column("'[)'")), "&&"),
            name="appointments_no_provider_overlap",
            using="gist",
            where=text("status = 'scheduled'"),
        )
    )
    Table(  # 006
        "users",
        metadata,
        _id(),
        Column("username", String(50), nullable=False),
        Column("email", String(255), nullable=False),
        Column("password_hash", Text, nullable=False),
        Column("role", String(30), nullable=False, server_default=text("'viewer'")),
        Column("is_active", Boolean, nullable=False, server_default=text("TRUE")),
        Column("created_at", TIMESTAMPTZ, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
        PrimaryKeyConstraint("id", name="users_pkey"),
        UniqueConstraint("username", name="users_username_key"),
        UniqueConstraint("email", name="users_email_key"),
        CheckConstraint("LENGTH(TRIM(username)) > 0", name="users_username_not_blank"),
        CheckConstraint("LENGTH(TRIM(email)) > 0", name="users_email_not_blank"),
        CheckConstraint(
            "role IN ('admin', 'clinician', 'scheduler', 'viewer')", name="users_valid_role"
        ),
        Index("idx_users_role", "role"),
    )
    Table(  # 007
        "audit_logs",
        metadata,
        _id(BigInteger),
        Column("user_id", Integer),
        Column("action", String(100), nullable=False),
        Column("resource_type", String(100), nullable=False),
        Column("resource_id", String(100)),
        Column("outcome", String(20), nullable=False),
        Column("details", JSONB),
        Column("created_at", TIMESTAMPTZ, nullable=False, server_default=text("NOW()")),
        PrimaryKeyConstraint("id", name="audit_logs_pkey"),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="audit_logs_user_id_fkey", ondelete="SET NULL"
        ),
        CheckConstraint("outcome IN ('success', 'failure')", name="audit_logs_outcome_check"),
    )
    return metadata


metadata = build_metadata()
