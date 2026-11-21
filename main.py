from typing import Literal
from urllib.parse import urlencode

from fastapi import (
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.exception_handlers import (
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from psycopg.errors import (
    ExclusionViolation,
    ForeignKeyViolation,
    RestrictViolation,
    UniqueViolation,
)

from appointment_service import (
    cancel_appointment,
    create_appointment,
    get_all_appointments,
    get_appointment_by_id,
    update_appointment,
)
from audit import log_audit_event
from audit_service import get_audit_logs
from auth_dependencies import (
    get_current_user,
    require_permission,
    require_roles,
)
from auth_service import authenticate_user
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
    Appointment,
    AppointmentCreate,
    AppointmentUpdate,
    Patient,
    PatientCreate,
    Provider,
    ProviderCreate,
)
from provider_service import (
    create_provider,
    delete_provider,
    get_all_providers,
    get_provider_by_id,
    search_providers,
    update_provider,
)
from token_service import (
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
)

app = FastAPI()


# Constraint violations that are client errors, per operation. Anything not
# listed still propagates, so unexpected database errors are not masked.
# Deletes catch RestrictViolation (ON DELETE RESTRICT, the current schema)
# and ForeignKeyViolation (ON DELETE NO ACTION) so either definition works.
APPOINTMENT_REFERENCE_CONSTRAINTS = {
    "appointments_patient_id_fkey": (404, "Patient not found"),
    "appointments_provider_id_fkey": (404, "Provider not found"),
}
PATIENT_DELETE_CONSTRAINTS = {
    "appointments_patient_id_fkey": (
        409,
        "Patient has appointments and cannot be deleted.",
    ),
}
PROVIDER_DELETE_CONSTRAINTS = {
    "appointments_provider_id_fkey": (
        409,
        "Provider has appointments and cannot be deleted.",
    ),
}
PROVIDER_WRITE_CONSTRAINTS = {
    "providers_email_key": (
        409,
        "A provider with this email already exists.",
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


@app.get("/")
def home():
    return {"application": "MediBridge", "status": "running"}


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "MediBridge API"}


@app.get("/audit-logs")
def list_audit_logs(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(require_permission("audit:read")),
):
    """Retrieve audit history for authorized administrators."""

    return {
        "limit": limit,
        "offset": offset,
        "items": get_audit_logs(
            limit=limit,
            offset=offset,
        ),
    }


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


@app.post(
    "/providers",
    response_model=Provider,
    status_code=201,
    tags=["Providers"],
    dependencies=[Depends(require_permission("providers:write"))],
)
def create_provider_endpoint(provider: ProviderCreate):
    """
    Create a new healthcare provider.
    """
    try:
        return create_provider(provider)
    except UniqueViolation as exc:
        raise_for_constraint(exc, PROVIDER_WRITE_CONSTRAINTS)


@app.get(
    "/providers",
    response_model=list[Provider],
    tags=["Providers"],
    dependencies=[Depends(require_permission("providers:read"))],
)
def list_providers_endpoint():
    """
    Retrieve all healthcare providers.
    """
    return get_all_providers()


@app.get(
    "/providers/{provider_id}",
    response_model=Provider,
    tags=["Providers"],
    dependencies=[Depends(require_permission("providers:read"))],
)
def get_provider_endpoint(provider_id: int):
    """
    Retrieve a healthcare provider by ID.
    """
    provider = get_provider_by_id(provider_id)

    if provider is None:
        raise HTTPException(
            status_code=404,
            detail="Provider not found",
        )

    return provider


@app.put(
    "/providers/{provider_id}",
    response_model=Provider,
    tags=["Providers"],
    dependencies=[Depends(require_permission("providers:write"))],
)
def update_provider_endpoint(
    provider_id: int,
    provider: ProviderCreate,
):
    """
    Update an existing healthcare provider.
    """
    try:
        updated_provider = update_provider(provider_id, provider)
    except UniqueViolation as exc:
        raise_for_constraint(exc, PROVIDER_WRITE_CONSTRAINTS)

    if updated_provider is None:
        raise HTTPException(
            status_code=404,
            detail="Provider not found",
        )

    return updated_provider


@app.delete(
    "/providers/{provider_id}",
    status_code=204,
    tags=["Providers"],
    dependencies=[Depends(require_permission("providers:write"))],
)
def delete_provider_endpoint(provider_id: int):
    """
    Delete a healthcare provider by ID.
    """
    try:
        deleted = delete_provider(provider_id)
    except (ForeignKeyViolation, RestrictViolation) as exc:
        raise_for_constraint(exc, PROVIDER_DELETE_CONSTRAINTS)

    if not deleted:
        raise HTTPException(
            status_code=404,
            detail="Provider not found",
        )

    return None


@app.post(
    "/appointments",
    response_model=Appointment,
    status_code=201,
    tags=["Appointments"],
    dependencies=[Depends(require_permission("appointments:write"))],
    responses={
        409: {
            "description": "Provider scheduling conflict",
            "content": {
                "application/json": {
                    "example": {
                        "detail": (
                            "This provider already has a scheduled "
                            "appointment during the requested time."
                        )
                    }
                }
            },
        }
    },
)
def create_appointment_endpoint(appointment: AppointmentCreate):
    try:
        return create_appointment(appointment)

    except ForeignKeyViolation as exc:
        raise_for_constraint(exc, APPOINTMENT_REFERENCE_CONSTRAINTS)

    except ExclusionViolation as exc:
        if exc.diag.constraint_name != "appointments_no_provider_overlap":
            raise

        raise HTTPException(
            status_code=409,
            detail=("This provider already has a scheduled appointment during the requested time."),
        ) from exc


@app.get(
    "/appointments",
    response_model=list[Appointment],
    tags=["Appointments"],
    dependencies=[Depends(require_permission("appointments:read"))],
)
def list_appointments_endpoint(
    patient_id: int | None = None,
    provider_id: int | None = None,
    status: Literal["scheduled", "completed", "cancelled"] | None = None,
):
    return get_all_appointments(
        patient_id=patient_id,
        provider_id=provider_id,
        status=status,
    )


@app.get(
    "/appointments/{appointment_id}",
    response_model=Appointment,
    tags=["Appointments"],
    dependencies=[Depends(require_permission("appointments:read"))],
)
def get_appointment_endpoint(appointment_id: int):
    appointment = get_appointment_by_id(appointment_id)

    if appointment is None:
        raise HTTPException(
            status_code=404,
            detail="Appointment not found",
        )

    return appointment


@app.put(
    "/appointments/{appointment_id}",
    response_model=Appointment,
    tags=["Appointments"],
    dependencies=[Depends(require_permission("appointments:write"))],
    responses={
        409: {
            "description": "Provider scheduling conflict",
            "content": {
                "application/json": {
                    "example": {
                        "detail": (
                            "This provider already has a scheduled "
                            "appointment during the requested time."
                        )
                    }
                }
            },
        }
    },
)
def update_appointment_endpoint(
    appointment_id: int,
    appointment: AppointmentUpdate,
):
    try:
        updated_appointment = update_appointment(
            appointment_id,
            appointment,
        )

    except ExclusionViolation as exc:
        if exc.diag.constraint_name != "appointments_no_provider_overlap":
            raise

        raise HTTPException(
            status_code=409,
            detail=("This provider already has a scheduled appointment during the requested time."),
        ) from exc

    if updated_appointment is None:
        raise HTTPException(
            status_code=404,
            detail="Appointment not found",
        )

    return updated_appointment


@app.patch(
    "/appointments/{appointment_id}/cancel",
    response_model=Appointment,
    tags=["Appointments"],
    dependencies=[Depends(require_permission("appointments:write"))],
)
def cancel_appointment_endpoint(appointment_id: int):
    cancelled_appointment = cancel_appointment(appointment_id)

    if cancelled_appointment is None:
        raise HTTPException(
            status_code=404,
            detail="Appointment not found",
        )

    return cancelled_appointment


@app.get("/auth/me", tags=["Authentication"])
def read_current_user(
    current_user: dict = Depends(get_current_user),
):
    """Return the currently authenticated user's information."""
    return current_user


@app.post("/auth/login", tags=["Authentication"])
def login(
    username: str = Form(...),
    password: str = Form(...),
):
    """Authenticate a user and issue a JWT access token."""

    user = authenticate_user(username, password)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(user["id"])

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }


@app.get("/auth/admin-check", tags=["Authentication"])
def admin_check(
    current_user: dict = Depends(require_roles("admin")),
):
    """Verify that the authenticated user has administrator access."""

    return {
        "message": "Administrator access granted",
        "username": current_user["username"],
        "role": current_user["role"],
    }


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
