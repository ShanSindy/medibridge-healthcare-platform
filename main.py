from urllib.parse import urlencode

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
)
from fastapi.exception_handlers import (
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from psycopg.errors import (
    ForeignKeyViolation,
    RestrictViolation,
)

from audit import log_audit_event
from auth_dependencies import (
    get_current_user,
    require_permission,
)
from database import (
    get_connection,
    get_identifiers_for_patients,
    get_patient_identifiers,
)
from db_errors import raise_for_constraint
from fhir import (
    create_operation_outcome,
    patient_to_fhir,
    provider_to_fhir,
    provider_to_practitioner_role,
)
from hl7_ack import generate_hl7_ack
from hl7_parser import parse_hl7_message
from hl7_service import (
    register_patient_from_hl7,
    update_patient_from_hl7,
)
from models import (
    Patient,
    PatientCreate,
)
from provider_service import (
    get_provider_by_id,
    search_providers,
)
from routers import appointments, audit_logs, auth, providers, system

app = FastAPI()


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


@app.exception_handler(RequestValidationError)
async def fhir_validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/fhir/"):
        return JSONResponse(
            status_code=422,
            content=create_operation_outcome(
                code="invalid", diagnostics="Invalid FHIR request parameters"
            ),
            media_type="application/fhir+json",
        )

    return await request_validation_exception_handler(request, exc)


app.include_router(system.router)


app.include_router(audit_logs.router)


@app.get(
    "/patients",
    response_model=list[Patient],
    dependencies=[Depends(require_permission("patients:read"))],
)
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


@app.get(
    "/patients/{patient_id}",
    response_model=Patient,
    dependencies=[Depends(require_permission("patients:read"))],
)
def get_patient(patient_id: int):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, first_name, last_name, date_of_birth, gender
                FROM patients
                WHERE id = %s
                """,
                (patient_id,),
            )

            row = cursor.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    return {
        "id": row[0],
        "first_name": row[1],
        "last_name": row[2],
        "date_of_birth": row[3],
        "gender": row[4],
    }


@app.post(
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


@app.delete(
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


@app.put(
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
            "providing FHIR R4 Patient, Practitioner, and PractitionerRole "
            "resource access and search."
        ),
        "kind": "instance",
        "software": {"name": "MediBridge Healthcare Platform", "version": "0.1.0"},
        "implementation": {"description": "MediBridge FHIR API"},
        "fhirVersion": "4.0.1",
        "format": ["application/fhir+json"],
        "rest": [
            {
                "mode": "server",
                "resource": [
                    {
                        "type": "Patient",
                        "profile": "http://hl7.org/fhir/StructureDefinition/Patient",
                        "interaction": [{"code": "read"}, {"code": "search-type"}],
                        "searchParam": [
                            {
                                "name": "family",
                                "type": "string",
                                "documentation": ("Search patients by family name."),
                            },
                            {
                                "name": "given",
                                "type": "string",
                                "documentation": ("Search patients by given name."),
                            },
                            {
                                "name": "identifier",
                                "type": "token",
                                "documentation": (
                                    "Search patients by identifier value "
                                    "or by system and value using "
                                    "system|value."
                                ),
                            },
                        ],
                    },
                    {
                        "type": "Practitioner",
                        "profile": "http://hl7.org/fhir/StructureDefinition/Practitioner",
                        "interaction": [{"code": "read"}, {"code": "search-type"}],
                        "searchParam": [
                            {
                                "name": "family",
                                "type": "string",
                                "documentation": ("Search practitioners by family name."),
                            },
                            {
                                "name": "given",
                                "type": "string",
                                "documentation": ("Search practitioners by given name."),
                            },
                        ],
                    },
                    {
                        "type": "PractitionerRole",
                        "profile": "http://hl7.org/fhir/StructureDefinition/PractitionerRole",
                        "interaction": [{"code": "read"}, {"code": "search-type"}],
                        "searchParam": [
                            {
                                "name": "specialty",
                                "type": "token",
                                "documentation": (
                                    "Currently supports case-insensitive prefix "
                                    "matching against specialty display text. "
                                    "FHIR token system|code matching is not yet "
                                    "implemented."
                                ),
                            }
                        ],
                    },
                ],
            }
        ],
    }


@app.get(
    "/fhir/Patient/{patient_id}",
    dependencies=[Depends(require_permission("patients:read"))],
)
def get_fhir_patient(patient_id: int):
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, first_name, last_name, date_of_birth, gender
                FROM patients
                WHERE id = %s
                """,
                (patient_id,),
            )

            row = cursor.fetchone()

    if row is None:
        return JSONResponse(
            status_code=404,
            content=create_operation_outcome(code="not-found", diagnostics="Patient not found"),
            media_type="application/fhir+json",
        )

    patient = Patient(
        id=row[0], first_name=row[1], last_name=row[2], date_of_birth=row[3], gender=row[4]
    )

    return patient_to_fhir(patient, identifiers=get_patient_identifiers(patient_id))


@app.get(
    "/fhir/Patient",
    dependencies=[Depends(require_permission("patients:read"))],
)
def get_fhir_patients(
    request: Request,
    family: str | None = None,
    given: str | None = None,
    identifier: str | None = None,
    count: int = Query(default=10, alias="_count", ge=1, le=100),
    offset: int = Query(default=0, alias="_offset", ge=0),
):
    # Escape SQL LIKE wildcards for prefix matching.
    def prefix_pattern(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
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

    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

    with get_connection() as conn:
        with conn.cursor() as cursor:
            # Retrieve one page of matching patients.
            # S608 reviewed: the WHERE clause is assembled only from fixed SQL fragments in this
            # function, and every user-supplied value is passed separately as a %s parameter.
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
                """,  # noqa: S608
                (*params, count, offset),
            )
            rows = cursor.fetchall()

            # Count all matches before pagination.
            # S608 reviewed: the WHERE clause is assembled only from fixed SQL fragments in this
            # function, and every user-supplied value is passed separately as a %s parameter.
            cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM patients p
                {where_clause}
                """,  # noqa: S608
                tuple(params),
            )
            total_matches = cursor.fetchone()[0]

    patients = [
        Patient(id=row[0], first_name=row[1], last_name=row[2], date_of_birth=row[3], gender=row[4])
        for row in rows
    ]

    # Retrieve identifiers for the entire page in one query.
    identifiers_by_patient = get_identifiers_for_patients([patient.id for patient in patients])

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

    links = [{"relation": "self", "url": page_url(offset)}]

    if offset + count < total_matches:
        links.append({"relation": "next", "url": page_url(offset + count)})

    if offset > 0:
        links.append({"relation": "previous", "url": page_url(max(0, offset - count))})

    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": total_matches,
        "link": links,
        "entry": [
            {
                "fullUrl": str(request.url_for("get_fhir_patient", patient_id=patient.id)),
                "resource": patient_to_fhir(
                    patient, identifiers=identifiers_by_patient.get(patient.id, [])
                ),
            }
            for patient in patients
        ],
    }


@app.post(
    "/hl7/adt",
    tags=["HL7 Integration"],
    dependencies=[Depends(require_permission("patients:write"))],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "text/plain": {
                    "schema": {"type": "string", "description": "Raw HL7 ADT^A04 message"}
                }
            },
        }
    },
)
async def receive_hl7_adt(request: Request):
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()

    if content_type != "text/plain":
        raise HTTPException(status_code=415, detail="Content-Type must be text/plain.")

    raw_message = (await request.body()).decode("utf-8", errors="replace")

    wants_hl7_ack = "application/hl7-v2" in request.headers.get("accept", "").lower()

    try:
        segments = parse_hl7_message(raw_message)
        message_type = segments["MSH"][0][8]

        if message_type == "ADT^A04":
            result = register_patient_from_hl7(raw_message)

        elif message_type == "ADT^A08":
            result = update_patient_from_hl7(raw_message)

        else:
            raise ValueError("Unsupported HL7 message type.")

    except ValueError as exc:
        if wants_hl7_ack and "segments" in locals():
            ack = generate_hl7_ack(segments, acknowledgment_code="AR", error_message=str(exc))
            return Response(content=ack, media_type="application/hl7-v2", status_code=400)

        raise HTTPException(status_code=400, detail=str(exc)) from exc

    except Exception:
        if wants_hl7_ack and "segments" in locals():
            ack = generate_hl7_ack(
                segments,
                acknowledgment_code="AE",
                error_message="Internal application processing error",
            )
            return Response(content=ack, media_type="application/hl7-v2", status_code=500)

        raise

    if wants_hl7_ack:
        ack = generate_hl7_ack(segments, acknowledgment_code="AA")
        return Response(content=ack, media_type="application/hl7-v2")

    return result


app.include_router(providers.router)


app.include_router(appointments.router)


app.include_router(auth.router)


@app.get(
    "/fhir/Practitioner/{practitioner_id}",
    dependencies=[Depends(require_permission("providers:read"))],
)
def get_fhir_practitioner(practitioner_id: int):
    provider = get_provider_by_id(practitioner_id)

    if provider is None:
        return JSONResponse(
            status_code=404,
            content=create_operation_outcome(
                code="not-found", diagnostics="Practitioner not found"
            ),
            media_type="application/fhir+json",
        )

    return JSONResponse(content=provider_to_fhir(provider), media_type="application/fhir+json")


@app.get(
    "/fhir/Practitioner",
    dependencies=[Depends(require_permission("providers:read"))],
)
def search_fhir_practitioners(
    request: Request,
    family: str | None = None,
    given: str | None = None,
    count: int = Query(10, alias="_count", ge=1, le=100),
    offset: int = Query(0, alias="_offset", ge=0),
):
    providers, total = search_providers(
        family=family,
        given=given,
        count=count,
        offset=offset,
    )

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
            "url": page_url(offset),
        }
    ]

    if offset + count < total:
        links.append(
            {
                "relation": "next",
                "url": page_url(offset + count),
            }
        )

    if offset > 0:
        links.append(
            {
                "relation": "previous",
                "url": page_url(max(0, offset - count)),
            }
        )

    bundle = {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": total,
        "link": links,
        "entry": [
            {
                "fullUrl": str(
                    request.url_for(
                        "get_fhir_practitioner",
                        practitioner_id=provider["id"],
                    )
                ),
                "resource": provider_to_fhir(provider),
            }
            for provider in providers
        ],
    }

    return JSONResponse(
        content=bundle,
        media_type="application/fhir+json",
    )


@app.get(
    "/fhir/PractitionerRole/{role_id}",
    dependencies=[Depends(require_permission("providers:read"))],
)
def get_fhir_practitioner_role(role_id: int):
    provider = get_provider_by_id(role_id)

    if provider is None:
        return JSONResponse(
            status_code=404,
            content=create_operation_outcome(
                code="not-found", diagnostics="PractitionerRole not found"
            ),
            media_type="application/fhir+json",
        )

    return JSONResponse(
        content=provider_to_practitioner_role(provider), media_type="application/fhir+json"
    )


@app.get(
    "/fhir/PractitionerRole",
    dependencies=[Depends(require_permission("providers:read"))],
)
def search_fhir_practitioner_roles(
    request: Request,
    specialty: str | None = None,
    count: int = Query(10, alias="_count", ge=1, le=100),
    offset: int = Query(0, alias="_offset", ge=0),
):
    providers, total = search_providers(
        specialty=specialty,
        count=count,
        offset=offset,
    )

    def page_url(page_offset: int) -> str:
        params = {}

        if specialty is not None:
            params["specialty"] = specialty

        params["_count"] = count
        params["_offset"] = page_offset

        return f"{request.url.path}?{urlencode(params)}"

    links = [
        {
            "relation": "self",
            "url": page_url(offset),
        }
    ]

    if offset + count < total:
        links.append(
            {
                "relation": "next",
                "url": page_url(offset + count),
            }
        )

    if offset > 0:
        links.append(
            {
                "relation": "previous",
                "url": page_url(max(0, offset - count)),
            }
        )

    bundle = {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": total,
        "link": links,
        "entry": [
            {
                "fullUrl": str(
                    request.url_for(
                        "get_fhir_practitioner_role",
                        role_id=provider["id"],
                    )
                ),
                "resource": provider_to_practitioner_role(provider),
            }
            for provider in providers
        ],
    }

    return JSONResponse(
        content=bundle,
        media_type="application/fhir+json",
    )
