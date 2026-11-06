
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
                (patient_id,)
            )

            rows = cursor.fetchall()

    return [
        {
            "system": row[0],
            "value": row[1]
        }
        for row in rows
    ]
