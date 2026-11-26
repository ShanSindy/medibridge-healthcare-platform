"""
Appointment persistence.

Reads (get_all_appointments, get_appointment_by_id) use the shared SQLAlchemy engine
through db_engine.read_only_connection(): PostgreSQL read-only transactions with the
engine's statement and lock timeouts. Writes (create, update, cancel) still use
database.get_connection(), unchanged; the overlap rule is enforced by PostgreSQL
(migration 005) and the appointments router translates its psycopg errors.
"""

from sqlalchemy import bindparam, column, select, table

from database import get_connection
from db_engine import read_only_connection
from models import AppointmentCreate, AppointmentUpdate

# The columns the reads select, in the order of the returned dictionaries. Untyped, so
# values come back exactly as psycopg returns them (timezone-aware datetimes), as before.
APPOINTMENTS = table(
    "appointments",
    column("id"),
    column("patient_id"),
    column("provider_id"),
    column("starts_at"),
    column("ends_at"),
    column("status"),
    column("reason"),
    column("created_at"),
)


def create_appointment(appointment: AppointmentCreate) -> dict:
    query = """
        INSERT INTO appointments (
            patient_id,
            provider_id,
            starts_at,
            ends_at,
            reason
        )
        VALUES (%s, %s, %s, %s, %s)
        RETURNING
            id,
            patient_id,
            provider_id,
            starts_at,
            ends_at,
            status,
            reason,
            created_at
    """

    values = (
        appointment.patient_id,
        appointment.provider_id,
        appointment.starts_at,
        appointment.ends_at,
        appointment.reason,
    )

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, values)
            row = cur.fetchone()

            columns = [
                "id",
                "patient_id",
                "provider_id",
                "starts_at",
                "ends_at",
                "status",
                "reason",
                "created_at",
            ]

            return dict(zip(columns, row, strict=True))


def get_all_appointments(
    patient_id: int | None = None,
    provider_id: int | None = None,
    status: str | None = None,
) -> list[dict]:
    """
    Appointments ordered by start time, then id. Each filter that is not None must match
    exactly (status is case-sensitive); together they combine with AND. Nothing is
    excluded by default, cancelled and completed appointments included.
    """
    conditions = []
    params: dict[str, object] = {}

    if patient_id is not None:
        conditions.append(APPOINTMENTS.c.patient_id == bindparam("patient_id"))
        params["patient_id"] = patient_id

    if provider_id is not None:
        conditions.append(APPOINTMENTS.c.provider_id == bindparam("provider_id"))
        params["provider_id"] = provider_id

    if status is not None:
        conditions.append(APPOINTMENTS.c.status == bindparam("status"))
        params["status"] = status

    query = (
        select(APPOINTMENTS)
        .where(*conditions)
        .order_by(APPOINTMENTS.c.starts_at, APPOINTMENTS.c.id)
    )

    with read_only_connection() as conn:
        rows = conn.execute(query, params).mappings().all()

    return [dict(row) for row in rows]


def get_appointment_by_id(appointment_id: int) -> dict | None:
    """The appointment with this id, or None."""
    query = select(APPOINTMENTS).where(APPOINTMENTS.c.id == bindparam("appointment_id"))

    with read_only_connection() as conn:
        row = conn.execute(query, {"appointment_id": appointment_id}).mappings().one_or_none()

    return dict(row) if row is not None else None


def update_appointment(
    appointment_id: int,
    appointment: AppointmentUpdate,
) -> dict | None:
    query = """
        UPDATE appointments
        SET
            starts_at = %s,
            ends_at = %s,
            status = %s,
            reason = %s
        WHERE id = %s
        RETURNING
            id,
            patient_id,
            provider_id,
            starts_at,
            ends_at,
            status,
            reason,
            created_at
    """

    values = (
        appointment.starts_at,
        appointment.ends_at,
        appointment.status,
        appointment.reason,
        appointment_id,
    )

    columns = [
        "id",
        "patient_id",
        "provider_id",
        "starts_at",
        "ends_at",
        "status",
        "reason",
        "created_at",
    ]

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, values)
            row = cur.fetchone()

            if row is None:
                return None

            return dict(zip(columns, row, strict=True))


def cancel_appointment(appointment_id: int) -> dict | None:
    query = """
        UPDATE appointments
        SET status = 'cancelled'
        WHERE id = %s
        RETURNING
            id,
            patient_id,
            provider_id,
            starts_at,
            ends_at,
            status,
            reason,
            created_at
    """

    columns = [
        "id",
        "patient_id",
        "provider_id",
        "starts_at",
        "ends_at",
        "status",
        "reason",
        "created_at",
    ]

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, (appointment_id,))
            row = cur.fetchone()

            if row is None:
                return None

            return dict(zip(columns, row, strict=True))
