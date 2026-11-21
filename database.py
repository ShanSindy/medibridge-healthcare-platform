import os

import psycopg
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    return psycopg.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def get_patient_identifiers(patient_id: int) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT system, value
                FROM patient_identifiers
                WHERE patient_id = %s
                ORDER BY id
                """,
                (patient_id,),
            )

            rows = cursor.fetchall()

    return [{"system": row[0], "value": row[1]} for row in rows]


def get_identifiers_for_patients(patient_ids: list[int]) -> dict[int, list[dict]]:
    if not patient_ids:
        return {}

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT patient_id, system, value
                FROM patient_identifiers
                WHERE patient_id = ANY(%s)
                ORDER BY patient_id, id
                """,
                (patient_ids,),
            )

            rows = cursor.fetchall()

    identifiers_by_patient = {patient_id: [] for patient_id in patient_ids}

    for patient_id, system, value in rows:
        identifiers_by_patient[patient_id].append({"system": system, "value": value})

    return identifiers_by_patient
