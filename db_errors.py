"""Translate known PostgreSQL integrity violations into API errors."""

from typing import NoReturn

from fastapi import HTTPException
from psycopg import errors

ConstraintErrors = dict[str, tuple[int, str]]


def raise_for_constraint(
    exc: errors.IntegrityError,
    known_constraints: ConstraintErrors,
) -> NoReturn:
    """
    Raise the HTTP error mapped to the violated constraint.

    The mapping is chosen per endpoint because one constraint can mean
    different things: `appointments_patient_id_fkey` fails on INSERT when the
    patient does not exist, but on DELETE when the patient still has
    appointments.

    Violations that are not in the mapping are re-raised unchanged, so an
    unexpected database problem is never reported as a client error.
    """
    constraint = exc.diag.constraint_name

    if constraint in known_constraints:
        status_code, detail = known_constraints[constraint]
        raise HTTPException(status_code=status_code, detail=detail) from exc

    raise exc
