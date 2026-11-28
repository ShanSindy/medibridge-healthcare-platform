"""
Translate known PostgreSQL integrity violations into API errors.

Works with both forms a database error arrives in: psycopg's own exceptions (code on
database.get_connection()) and SQLAlchemy's wrappers (code on the shared engine), which
carry the psycopg exception in `.orig`. The SQLSTATE and constraint name are read from
the psycopg exception in either case.
"""

from typing import NoReturn

import psycopg
from fastapi import HTTPException
from sqlalchemy import exc as sa_exc

ConstraintErrors = dict[str, tuple[int, str]]

# SQLSTATE class 23: integrity constraint violations (unique, foreign key, check,
# exclusion, not null, restrict). Only these are ever mapped to client errors.
INTEGRITY_CLASS = "23"


def psycopg_error(exc: BaseException) -> psycopg.Error | None:
    """The psycopg exception behind `exc`: `exc` itself, or the `.orig` of a SQLAlchemy
    DBAPIError; None for anything else."""
    if isinstance(exc, psycopg.Error):
        return exc
    if isinstance(exc, sa_exc.DBAPIError) and isinstance(exc.orig, psycopg.Error):
        return exc.orig
    return None


def sqlstate(exc: BaseException) -> str | None:
    """The SQLSTATE of a native or wrapped psycopg exception, or None."""
    native = psycopg_error(exc)
    return native.sqlstate if native is not None else None


def violated_constraint(exc: BaseException) -> str | None:
    """
    The constraint named by an integrity violation (SQLSTATE class 23), native or
    wrapped; None for any other error, so a constraint name can never turn an unrelated
    failure into a client error.
    """
    native = psycopg_error(exc)
    if native is None or not (native.sqlstate or "").startswith(INTEGRITY_CLASS):
        return None
    return native.diag.constraint_name


def raise_for_constraint(
    exc: BaseException,
    known_constraints: ConstraintErrors,
) -> NoReturn:
    """
    Raise the HTTP error mapped to the violated constraint.

    The mapping is chosen per endpoint because one constraint can mean
    different things: `appointments_patient_id_fkey` fails on INSERT when the
    patient does not exist, but on DELETE when the patient still has
    appointments.

    Violations that are not in the mapping, and errors that are not integrity
    violations, are re-raised unchanged (in the form they arrived in), so an
    unexpected database problem is never reported as a client error. The HTTP error
    carries only the mapped detail, never the database message.
    """
    constraint = violated_constraint(exc)

    if constraint is not None and constraint in known_constraints:
        status_code, detail = known_constraints[constraint]
        raise HTTPException(status_code=status_code, detail=detail) from exc

    raise exc
