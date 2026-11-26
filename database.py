import os

import psycopg
from dotenv import load_dotenv
from sqlalchemy import BigInteger, any_, bindparam, column, select, table
from sqlalchemy.dialects.postgresql import ARRAY

from db_engine import read_only_connection

load_dotenv()

# Read by the identifier helpers below through the shared engine (read-only transactions
# with its statement and lock timeouts). Untyped, so values come back as psycopg returns
# them, as before.
PATIENT_IDENTIFIERS = table(
    "patient_identifiers",
    column("id"),
    column("patient_id"),
    column("system"),
    column("value"),
)


def get_connection():
    return psycopg.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def get_patient_identifiers(patient_id: int) -> list[dict]:
    identifiers = PATIENT_IDENTIFIERS.c
    query = (
        select(identifiers.system, identifiers.value)
        .where(identifiers.patient_id == bindparam("patient_id"))
        .order_by(identifiers.id)
    )

    with read_only_connection() as conn:
        rows = conn.execute(query, {"patient_id": patient_id}).all()

    return [{"system": row[0], "value": row[1]} for row in rows]


def get_identifiers_for_patients(patient_ids: list[int]) -> dict[int, list[dict]]:
    if not patient_ids:
        return {}

    # One query for all the patients, as before: patient_id = ANY(the ids as a BIGINT[]
    # array), which accepts every id the INTEGER column can hold, and more.
    identifiers = PATIENT_IDENTIFIERS.c
    query = (
        select(identifiers.patient_id, identifiers.system, identifiers.value)
        .where(identifiers.patient_id == any_(bindparam("patient_ids", type_=ARRAY(BigInteger))))
        .order_by(identifiers.patient_id, identifiers.id)
    )

    with read_only_connection() as conn:
        rows = conn.execute(query, {"patient_ids": patient_ids}).all()

    identifiers_by_patient = {patient_id: [] for patient_id in patient_ids}

    for patient_id, system, value in rows:
        identifiers_by_patient[patient_id].append({"system": system, "value": value})

    return identifiers_by_patient
