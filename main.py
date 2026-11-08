
from database import (
    get_connection,
    get_patient_identifiers,
    get_identifiers_for_patients
)
from models import Patient, PatientCreate
from fastapi import FastAPI, HTTPException, Query, Request, Body
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fhir import patient_to_fhir, create_operation_outcome
from urllib.parse import urlencode
from hl7_service import (
    register_patient_from_hl7,
    update_patient_from_hl7,
)
from hl7_parser import parse_hl7_message


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
                                "documentation": (
                                    "Search patients by family name."
                                )
                            },
                            {
                                "name": "given",
                                "type": "string",
                                "documentation": (
                                    "Search patients by given name."
                                )
                            },
                            {
                                "name": "identifier",
                                "type": "token",
                                "documentation": (
                                    "Search patients by identifier value "
                                    "or by system and value using "
                                    "system|value."
                                )
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

    return patient_to_fhir(
        patient,
        identifiers=get_patient_identifiers(patient_id)
    )






@app.get("/fhir/Patient")
def get_fhir_patients(
    request: Request,
    family: str | None = None,
    given: str | None = None,
    identifier: str | None = None,
    count: int = Query(default=10, alias="_count", ge=1, le=100),
    offset: int = Query(default=0, alias="_offset", ge=0)
):
    # Escape SQL LIKE wildcards for prefix matching.
    def prefix_pattern(value: str) -> str:
        escaped = (
            value.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        return f"{escaped}%"

    # Build search filters and their SQL parameters.
    conditions = []
    params = []

    if family is not None:
        conditions.append("p.last_name ILIKE %s")
        params.append(prefix_pattern(family))

    if given is not None:
        conditions.append("p.first_name ILIKE %s")
        params.append(prefix_pattern(given))

    if identifier is not None:
        if "|" in identifier:
            system, value = identifier.split("|", 1)

            conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM patient_identifiers pi
                    WHERE pi.patient_id = p.id
                      AND pi.system = %s
                      AND pi.value = %s
                )
                """
            )
            params.extend([system, value])
        else:
            conditions.append(
                """
                EXISTS (
                    SELECT 1
                    FROM patient_identifiers pi
                    WHERE pi.patient_id = p.id
                      AND pi.value = %s
                )
                """
            )
            params.append(identifier)

    where_clause = (
        "WHERE " + " AND ".join(conditions)
        if conditions
        else ""
    )

    with get_connection() as conn:
        with conn.cursor() as cursor:
            # Retrieve one page of matching patients.
            cursor.execute(
                f"""
                SELECT
                    p.id,
                    p.first_name,
                    p.last_name,
                    p.date_of_birth,
                    p.gender
                FROM patients p
                {where_clause}
                ORDER BY p.id
                LIMIT %s OFFSET %s
                """,
                (*params, count, offset)
            )
            rows = cursor.fetchall()

            # Count all matches before pagination.
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM patients p
                {where_clause}
                """,
                tuple(params)
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

    # Retrieve identifiers for the entire page in one query.
    identifiers_by_patient = get_identifiers_for_patients(
        [patient.id for patient in patients]
    )


    # Build pagination links and preserve search filters.
    def page_url(page_offset: int) -> str:
        url_params = {}

        if family is not None:
            url_params["family"] = family

        if given is not None:
            url_params["given"] = given

        if identifier is not None:
            url_params["identifier"] = identifier

        url_params["_count"] = count
        url_params["_offset"] = page_offset

        return f"{request.url.path}?{urlencode(url_params)}"

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
                "resource": patient_to_fhir(
                    patient,
                    identifiers=identifiers_by_patient.get(
                        patient.id, []
                    )
                )
            }
            for patient in patients
        ]
    }


@app.post(
    "/hl7/adt",
    tags=["HL7 Integration"],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "text/plain": {
                    "schema": {
                        "type": "string",
                        "description": "Raw HL7 ADT^A04 message"
                    }
                }
            }
        }
    }
)

async def receive_hl7_adt(request: Request):
    content_type = request.headers.get(
        "content-type", ""
    ).split(";")[0].strip().lower()

    if content_type != "text/plain":
        raise HTTPException(
            status_code=415,
            detail="Content-Type must be text/plain."
        )

    raw_message = (
        await request.body()
    ).decode("utf-8", errors="replace")

    try:
        segments = parse_hl7_message(raw_message)
        message_type = segments["MSH"][0][8]

        if message_type == "ADT^A04":
            result = register_patient_from_hl7(raw_message)

        elif message_type == "ADT^A08":
            result = update_patient_from_hl7(raw_message)

        else:
            raise ValueError(
                "Unsupported HL7 message type."
            )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        ) from exc

    return result
