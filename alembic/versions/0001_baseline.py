"""
Baseline: the schema of the historical migrations 000-007.

Revision ID: 0001_baseline
Revises: none

The baseline runs the historical SQL migrations themselves, in order, after checking
each against a pinned SHA-256 (of the text with the UTF-8 byte order mark removed, as
migrate.py reads it, and LF line endings). So a fresh Alembic install builds exactly the
schema migrate.py builds, and an edit to a historical migration stops it before any
change. It does not write the historical schema_migrations ledger; Alembic records the
version in alembic_version.

Downgrading the baseline would drop every table and is not supported.
"""

import hashlib
from pathlib import Path

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
PINNED = {
    "000_create_patients.sql": "16a91c7754eb0411606b5587387ce7ca72ba3f7e99d66a5d3d1dd635ce347255",
    "001_create_patient_identifiers.sql": (
        "87253ad6653e0c6e5d2c850b85efbc4f03bc6a175149e410b72ae8505c2950ca"
    ),
    "002_create_hl7_messages.sql": (
        "d7b0dd3ab758bad98504e936f43203466e5791955100571527ac829932d5fe49"
    ),
    "003_create_providers.sql": "b688aa194612ca245e9d935d83a05207ba193d58cb4ad39f9de260209845a425",
    "004_create_appointments.sql": (
        "0dc39783e1b517b9b57b5f1d46dbc3e5a4e29004cf8852387e3321b735767760"
    ),
    "005_prevent_appointment_overlaps.sql": (
        "e75524d9935046e6870cb3b51c39df64b9bbc205e201f2ae280121f8fd6feca0"
    ),
    "006_create_users.sql": "58eeb6ec84fa3142cfd6a673afdc9a3d6da09a0348ee690eb0017f2a5cbf9e2f",
    "007_create_audit_logs.sql": "d4dacdd65896beed1da2e90b980e70c3acb55e111e9d683e15018f3382ef668f",
}


def verified_sql(migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """The historical migrations in order; raises if any differs from its pinned hash."""
    found = sorted(p.name for p in migrations_dir.glob("*.sql"))
    if found != sorted(PINNED):
        raise RuntimeError("The historical migration files differ from the pinned set.")
    statements = []
    for name, digest in PINNED.items():
        text = (migrations_dir / name).read_text(encoding="utf-8-sig")
        if hashlib.sha256(text.replace("\r\n", "\n").encode()).hexdigest() != digest:
            raise RuntimeError(f"{name} differs from the pinned historical migration.")
        statements.append(text)
    return statements


def upgrade() -> None:
    statements = verified_sql()  # every file is checked before anything runs
    raw = op.get_bind().connection.driver_connection
    for sql in statements:
        raw.execute(sql)  # no parameters: psycopg runs the whole file, like migrate.py


def downgrade() -> None:
    raise NotImplementedError("Downgrading the baseline is not supported.")
