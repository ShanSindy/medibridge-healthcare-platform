from database import get_connection
from models import AppointmentCreate, AppointmentUpdate


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

            return dict(zip(columns, row))


def get_all_appointments(
    patient_id: int | None = None,
    provider_id: int | None = None,
    status: str | None = None,
) -> list[dict]:
    query = """
        SELECT
            id,
            patient_id,
            provider_id,
            starts_at,
            ends_at,
            status,
            reason,
            created_at
        FROM appointments
        WHERE 1 = 1
    """

    values = []

    if patient_id is not None:
        query += " AND patient_id = %s"
        values.append(patient_id)

    if provider_id is not None:
        query += " AND provider_id = %s"
        values.append(provider_id)

    if status is not None:
        query += " AND status = %s"
        values.append(status)

    query += " ORDER BY starts_at, id"

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
            cur.execute(query, tuple(values))
            rows = cur.fetchall()

            return [dict(zip(columns, row)) for row in rows]


def get_appointment_by_id(appointment_id: int) -> dict | None:
    query = """
        SELECT
            id,
            patient_id,
            provider_id,
            starts_at,
            ends_at,
            status,
            reason,
            created_at
        FROM appointments
        WHERE id = %s
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

            return dict(zip(columns, row))


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

            return dict(zip(columns, row))


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

            return dict(zip(columns, row))
