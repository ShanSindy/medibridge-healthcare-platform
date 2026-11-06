
from database import get_connection
from models import Patient, PatientCreate
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fhir import patient_to_fhir, create_operation_outcome
from urllib.parse import urlencode

app = FastAPI()


@app.exception_handler(RequestValidationError)
async def fhir_validation_exception_handler(
    request: Request,
    exc: RequestValidationError
):
    if request.url.path.startswith("/fhir/"):
        return JSONResponse(
            status_code=422,
            content=create_operation_outcome(
                code="invalid",
                diagnostics="Invalid FHIR request parameters"
            ),
            media_type="application/fhir+json"
        )

    return await request_validation_exception_handler(
        request,
        exc
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


@app.post("/patients", response_model=Patient, status_code=201)
def create_patient(patient: PatientCreate):
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
                    patient.gender
                )
            )

            row = cursor.fetchone()

    return {
        "id": row[0],
        "first_name": row[1],
        "last_name": row[2],
        "date_of_birth": row[3],
        "gender": row[4]
    }

@app.delete("/patients/{patient_id}", status_code=204)
def delete_patient(patient_id: int):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM patients WHERE id = %s RETURNING id",
                (patient_id,)
            )

            deleted_patient = cursor.fetchone()

            if deleted_patient is None:
                raise HTTPException(
                    status_code=404,
                    detail="Patient not found"
                )

@app.put("/patients/{patient_id}", response_model=Patient)
def update_patient(patient_id: int, patient: PatientCreate):
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
                    patient_id
                )
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
        "gender": row[4]
    }




@app.get("/fhir/metadata")
def get_fhir_metadata():
    return {
        "resourceType": "CapabilityStatement",
        "name": "MediBridgeCapabilityStatement",
        "title": "MediBridge FHIR R4 Capability Statement",
        "status": "active",
        "date": "2026-11-06",
        "publisher": "MediBridge Healthcare Platform",
        "description": (
            "MediBridge is a healthcare interoperability platform "
            "providing FHIR R4 Patient resource access and search."
        ),
        "kind": "instance",
        "software": {
            "name": "MediBridge Healthcare Platform",
            "version": "0.1.0"
        },
        "implementation": {
            "description": "MediBridge FHIR API"
        },
        "fhirVersion": "4.0.1",
        "format": ["application/fhir+json"],
        "rest": [
            {
                "mode": "server",
                "resource": [
                    {
                        "type": "Patient",
                        "profile": "http://hl7.org/fhir/StructureDefinition/Patient",
                        "interaction": [
                            {"code": "read"},
                            {"code": "search-type"}
                        ],
                        "searchParam": [
                            {
                                "name": "family",
                                "type": "string",
                                "documentation": "Search patients by family name."
                            },
                            {
                                "name": "given",
                                "type": "string",
                                "documentation": "Search patients by given name."
                            }
                        ]
                    }
                ]
            }
        ]
    }


@app.get("/fhir/Patient/{patient_id}")
def get_fhir_patient(patient_id: int):
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
        return JSONResponse(
            status_code=404,
            content=create_operation_outcome(
                code="not-found",
                diagnostics="Patient not found"
            ),
            media_type="application/fhir+json"
        )

    patient = Patient(
        id=row[0],
        first_name=row[1],
        last_name=row[2],
        date_of_birth=row[3],
        gender=row[4]
    )

    return patient_to_fhir(patient)




@app.get("/fhir/Patient")
def get_fhir_patients(
    request: Request,
    family: str | None = None,
    given: str | None = None,
    count: int = Query(default=10, alias="_count", ge=1, le=100),
    offset: int = Query(default=0, alias="_offset", ge=0)
):
    with get_connection() as conn:
        with conn.cursor() as cursor:

            if family is not None and given is not None:
                cursor.execute(
                    """
                    SELECT id, first_name, last_name, date_of_birth, gender
                    FROM patients
                    WHERE LOWER(last_name) = LOWER(%s)
                      AND LOWER(first_name) = LOWER(%s)
                    ORDER BY id
                    LIMIT %s OFFSET %s
                    """,
                    (family, given, count, offset)
                )

            elif family is not None:
                cursor.execute(
                    """
                    SELECT id, first_name, last_name, date_of_birth, gender
                    FROM patients
                    WHERE LOWER(last_name) = LOWER(%s)
                    ORDER BY id
                    LIMIT %s OFFSET %s
                    """,
                    (family, count, offset)
                )

            elif given is not None:
                cursor.execute(
                    """
                    SELECT id, first_name, last_name, date_of_birth, gender
                    FROM patients
                    WHERE LOWER(first_name) = LOWER(%s)
                    ORDER BY id
                    LIMIT %s OFFSET %s
                    """,
                    (given, count, offset)
                )

            else:
                cursor.execute(
                    """
                    SELECT id, first_name, last_name, date_of_birth, gender
                    FROM patients
                    ORDER BY id
                    LIMIT %s OFFSET %s
                    """,
                    (count, offset)
                )

            rows = cursor.fetchall()
            
            if family is not None and given is not None:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM patients
                    WHERE LOWER(last_name) = LOWER(%s)
                      AND LOWER(first_name) = LOWER(%s)
                    """,
                    (family, given)
                )

            elif family is not None:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM patients
                    WHERE LOWER(last_name) = LOWER(%s)
                    """,
                    (family,)
                )

            elif given is not None:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM patients
                    WHERE LOWER(first_name) = LOWER(%s)
                    """,
                    (given,)
                )

            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM patients"
                )

            total_matches = cursor.fetchone()[0]


    patients = [
        Patient(
            id=row[0],
            first_name=row[1],
            last_name=row[2],
            date_of_birth=row[3],
            gender=row[4]
        )
        for row in rows
    ]

    
    def page_url(page_offset: int) -> str:
        params = {}

        if family is not None:
            params["family"] = family

        if given is not None:
            params["given"] = given

        params["_count"] = count
        params["_offset"] = page_offset

        return f"{request.url.path}?{urlencode(params)}"

    links = [
        {
            "relation": "self",
            "url": page_url(offset)
        }
    ]

    if offset + count < total_matches:
        links.append({
            "relation": "next",
            "url": page_url(offset + count)
        })

    if offset > 0:
        links.append({
            "relation": "previous",
            "url": page_url(max(0, offset - count))
        })


   
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": total_matches,
        "link": links,
        "entry": [
            {
                "fullUrl": str(
                    request.url_for(
                        "get_fhir_patient",
                        patient_id=patient.id
                    )
                ),
                "resource": patient_to_fhir(patient)
            }
            for patient in patients
        ]
    }

