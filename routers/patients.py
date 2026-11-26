"""Patient record endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import ForeignKeyViolation, RestrictViolation

from audit import log_audit_event
from auth_dependencies import get_current_user, require_permission
from database import get_connection
from db_errors import raise_for_constraint
from models import Patient, PatientCreate
from patient_service import get_all_patients, get_patient_by_id

router = APIRouter()

# Constraint violations that are client errors, per operation. Anything not
# listed still propagates, so unexpected database errors are not masked.
# Deletes catch RestrictViolation (ON DELETE RESTRICT, the current schema)
# and ForeignKeyViolation (ON DELETE NO ACTION) so either definition works.
PATIENT_DELETE_CONSTRAINTS = {
    "appointments_patient_id_fkey": (
        409,
        "Patient has appointments and cannot be deleted.",
    ),
}


@router.get(
    "/patients",
    response_model=list[Patient],
    dependencies=[Depends(require_permission("patients:read"))],
)
def get_patients():
    return get_all_patients()


@router.get(
    "/patients/{patient_id}",
    response_model=Patient,
    dependencies=[Depends(require_permission("patients:read"))],
)
def get_patient(patient_id: int):
    patient = get_patient_by_id(patient_id)

    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    return patient


@router.post(
    "/patients",
    response_model=Patient,
    status_code=201,
    dependencies=[Depends(require_permission("patients:write"))],
)
def create_patient(
    patient: PatientCreate,
    current_user: dict = Depends(get_current_user),
):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO patients (
                    first_name,
                    last_name,
                    date_of_birth,
                    gender
                )
                VALUES (%s, %s, %s, %s)
                RETURNING id, first_name, last_name, date_of_birth, gender
                """,
                (
                    patient.first_name,
                    patient.last_name,
                    patient.date_of_birth,
                    patient.gender,
                ),
            )

            row = cursor.fetchone()

        log_audit_event(
            action="patient.create",
            resource_type="patient",
            resource_id=str(row[0]),
            outcome="success",
            user_id=current_user["id"],
            details={"source": "api"},
            connection=conn,
        )

    return {
        "id": row[0],
        "first_name": row[1],
        "last_name": row[2],
        "date_of_birth": row[3],
        "gender": row[4],
    }


@router.delete(
    "/patients/{patient_id}",
    status_code=204,
    dependencies=[Depends(require_permission("patients:write"))],
)
def delete_patient(
    patient_id: int,
    current_user: dict = Depends(get_current_user),
):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    "DELETE FROM patients WHERE id = %s RETURNING id",
                    (patient_id,),
                )
            except (ForeignKeyViolation, RestrictViolation) as exc:
                raise_for_constraint(exc, PATIENT_DELETE_CONSTRAINTS)

            deleted_patient = cursor.fetchone()

            if deleted_patient is None:
                raise HTTPException(
                    status_code=404,
                    detail="Patient not found",
                )

            log_audit_event(
                user_id=current_user["id"],
                action="patient.delete",
                resource_type="patient",
                resource_id=str(patient_id),
                outcome="success",
                connection=conn,
            )


@router.put(
    "/patients/{patient_id}",
    response_model=Patient,
    dependencies=[Depends(require_permission("patients:write"))],
)
def update_patient(
    patient_id: int,
    patient: PatientCreate,
    current_user: dict = Depends(get_current_user),
):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE patients
                SET first_name = %s,
                    last_name = %s,
                    date_of_birth = %s,
                    gender = %s
                WHERE id = %s
                RETURNING id, first_name, last_name, date_of_birth, gender
                """,
                (
                    patient.first_name,
                    patient.last_name,
                    patient.date_of_birth,
                    patient.gender,
                    patient_id,
                ),
            )

            row = cursor.fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Patient not found",
                )

            log_audit_event(
                user_id=current_user["id"],
                action="patient.update",
                resource_type="patient",
                resource_id=str(patient_id),
                outcome="success",
                connection=conn,
            )

    return {
        "id": row[0],
        "first_name": row[1],
        "last_name": row[2],
        "date_of_birth": row[3],
        "gender": row[4],
    }
