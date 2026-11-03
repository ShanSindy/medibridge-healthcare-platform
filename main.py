
from database import get_connection
from models import Patient
from fastapi import FastAPI, HTTPException

app = FastAPI(
    title="MediBridge",
    description="Healthcare Integration & Interoperability Platform",
    version="0.1.0"
)

@app.get("/")
def home():
    return {
        "application": "MediBridge",
        "status": "running"
    }

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "MediBridge API"
    }


@app.get("/patients", response_model=list[Patient])
def get_patients():
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, first_name, last_name, date_of_birth, gender
                FROM patients
                ORDER BY id
                """
            )

            rows = cursor.fetchall()

    return [
        {
            "id": row[0],
            "first_name": row[1],
            "last_name": row[2],
            "date_of_birth": row[3],
            "gender": row[4],
        }
        for row in rows
    ]


@app.get("/patients/{patient_id}", response_model=Patient)
def get_patient(patient_id: int):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, first_name, last_name, date_of_birth, gender
                FROM patients
                WHERE id = %s
                """,
                (patient_id,)
            )

            row = cursor.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Patient not found"
        )

    return {
        "id": row[0],
        "first_name": row[1],
        "last_name": row[2],
        "date_of_birth": row[3],
        "gender": row[4],
    }

