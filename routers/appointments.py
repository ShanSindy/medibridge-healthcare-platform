"""Appointment scheduling endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import ExclusionViolation, ForeignKeyViolation

from appointment_service import (
    cancel_appointment,
    create_appointment,
    get_all_appointments,
    get_appointment_by_id,
    update_appointment,
)
from auth_dependencies import require_permission
from db_errors import raise_for_constraint
from models import Appointment, AppointmentCreate, AppointmentUpdate

router = APIRouter()

# Constraint violations that are client errors, per operation. Anything not
# listed still propagates, so unexpected database errors are not masked.
APPOINTMENT_REFERENCE_CONSTRAINTS = {
    "appointments_patient_id_fkey": (404, "Patient not found"),
    "appointments_provider_id_fkey": (404, "Provider not found"),
}


@router.post(
    "/appointments",
    response_model=Appointment,
    status_code=201,
    tags=["Appointments"],
    dependencies=[Depends(require_permission("appointments:write"))],
    responses={
        409: {
            "description": "Provider scheduling conflict",
            "content": {
                "application/json": {
                    "example": {
                        "detail": (
                            "This provider already has a scheduled "
                            "appointment during the requested time."
                        )
                    }
                }
            },
        }
    },
)
def create_appointment_endpoint(appointment: AppointmentCreate):
    try:
        return create_appointment(appointment)

    except ForeignKeyViolation as exc:
        raise_for_constraint(exc, APPOINTMENT_REFERENCE_CONSTRAINTS)

    except ExclusionViolation as exc:
        if exc.diag.constraint_name != "appointments_no_provider_overlap":
            raise

        raise HTTPException(
            status_code=409,
            detail=("This provider already has a scheduled appointment during the requested time."),
        ) from exc


@router.get(
    "/appointments",
    response_model=list[Appointment],
    tags=["Appointments"],
    dependencies=[Depends(require_permission("appointments:read"))],
)
def list_appointments_endpoint(
    patient_id: int | None = None,
    provider_id: int | None = None,
    status: Literal["scheduled", "completed", "cancelled"] | None = None,
):
    return get_all_appointments(
        patient_id=patient_id,
        provider_id=provider_id,
        status=status,
    )


@router.get(
    "/appointments/{appointment_id}",
    response_model=Appointment,
    tags=["Appointments"],
    dependencies=[Depends(require_permission("appointments:read"))],
)
def get_appointment_endpoint(appointment_id: int):
    appointment = get_appointment_by_id(appointment_id)

    if appointment is None:
        raise HTTPException(
            status_code=404,
            detail="Appointment not found",
        )

    return appointment


@router.put(
    "/appointments/{appointment_id}",
    response_model=Appointment,
    tags=["Appointments"],
    dependencies=[Depends(require_permission("appointments:write"))],
    responses={
        409: {
            "description": "Provider scheduling conflict",
            "content": {
                "application/json": {
                    "example": {
                        "detail": (
                            "This provider already has a scheduled "
                            "appointment during the requested time."
                        )
                    }
                }
            },
        }
    },
)
def update_appointment_endpoint(
    appointment_id: int,
    appointment: AppointmentUpdate,
):
    try:
        updated_appointment = update_appointment(
            appointment_id,
            appointment,
        )

    except ExclusionViolation as exc:
        if exc.diag.constraint_name != "appointments_no_provider_overlap":
            raise

        raise HTTPException(
            status_code=409,
            detail=("This provider already has a scheduled appointment during the requested time."),
        ) from exc

    if updated_appointment is None:
        raise HTTPException(
            status_code=404,
            detail="Appointment not found",
        )

    return updated_appointment


@router.patch(
    "/appointments/{appointment_id}/cancel",
    response_model=Appointment,
    tags=["Appointments"],
    dependencies=[Depends(require_permission("appointments:write"))],
)
def cancel_appointment_endpoint(appointment_id: int):
    cancelled_appointment = cancel_appointment(appointment_id)

    if cancelled_appointment is None:
        raise HTTPException(
            status_code=404,
            detail="Appointment not found",
        )

    return cancelled_appointment
