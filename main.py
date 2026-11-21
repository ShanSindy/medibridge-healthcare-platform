from urllib.parse import urlencode

from fastapi import (
    Depends,
    FastAPI,
    Query,
    Request,
)
from fastapi.exception_handlers import (
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from auth_dependencies import (
    require_permission,
)
from database import (
    get_connection,
    get_identifiers_for_patients,
    get_patient_identifiers,
)
from fhir import (
    create_operation_outcome,
    patient_to_fhir,
    provider_to_fhir,
    provider_to_practitioner_role,
)
from models import (
    Patient,
)
from provider_service import (
    get_provider_by_id,
    search_providers,
)
from routers import appointments, audit_logs, auth, hl7, patients, providers, system

app = FastAPI()


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


app.include_router(patients.router)


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


app.include_router(hl7.router)


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
