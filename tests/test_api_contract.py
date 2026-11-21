"""
API contract regression tests.

The expected contract below is written out explicitly from the approved
baseline (main @ 51abb30). It is not derived from the application under test,
so any change to a route, its handler name, operation ID, access rule,
response model, status code or tags fails here and must be made deliberately.

Routing is checked through the application's real dispatch. Authorization is
checked two ways: statically (which dependency guards each
route) and behaviourally, by calling every operation unauthenticated and as
each of the four roles. Behavioural calls run against the session's disposable
database, which is empty at the start of every test, and use requests that
cannot write: unknown IDs, or request bodies that fail validation.
"""

from typing import get_args, get_origin

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient

import auth_dependencies
from main import app

ROLES = ("admin", "clinician", "scheduler", "viewer")
PUBLIC = "PUBLIC"
AUTHENTICATED = "AUTHENTICATED"

# (method, path, handler/route name, operationId, access, response model,
#  declared status code (None = default 200), tags, documented success codes)
CONTRACT = [
    ("GET", "/", "home", "home__get", PUBLIC, None, None, (), ["200"]),
    ("GET", "/health", "health_check", "health_check_health_get", PUBLIC, None, None, (), ["200"]),
    (
        "GET",
        "/audit-logs",
        "list_audit_logs",
        "list_audit_logs_audit_logs_get",
        ("admin",),
        None,
        None,
        (),
        ["200"],
    ),
    (
        "GET",
        "/patients",
        "get_patients",
        "get_patients_patients_get",
        ("admin", "clinician", "scheduler"),
        "list[Patient]",
        None,
        (),
        ["200"],
    ),
    (
        "GET",
        "/patients/{patient_id}",
        "get_patient",
        "get_patient_patients__patient_id__get",
        ("admin", "clinician", "scheduler"),
        "Patient",
        None,
        (),
        ["200"],
    ),
    (
        "POST",
        "/patients",
        "create_patient",
        "create_patient_patients_post",
        ("admin", "clinician"),
        "Patient",
        201,
        (),
        ["201"],
    ),
    (
        "DELETE",
        "/patients/{patient_id}",
        "delete_patient",
        "delete_patient_patients__patient_id__delete",
        ("admin", "clinician"),
        None,
        204,
        (),
        ["204"],
    ),
    (
        "PUT",
        "/patients/{patient_id}",
        "update_patient",
        "update_patient_patients__patient_id__put",
        ("admin", "clinician"),
        "Patient",
        None,
        (),
        ["200"],
    ),
    (
        "GET",
        "/fhir/metadata",
        "get_fhir_metadata",
        "get_fhir_metadata_fhir_metadata_get",
        PUBLIC,
        None,
        None,
        (),
        ["200"],
    ),
    (
        "GET",
        "/fhir/Patient/{patient_id}",
        "get_fhir_patient",
        "get_fhir_patient_fhir_Patient__patient_id__get",
        ("admin", "clinician", "scheduler"),
        None,
        None,
        (),
        ["200"],
    ),
    (
        "GET",
        "/fhir/Patient",
        "get_fhir_patients",
        "get_fhir_patients_fhir_Patient_get",
        ("admin", "clinician", "scheduler"),
        None,
        None,
        (),
        ["200"],
    ),
    (
        "POST",
        "/hl7/adt",
        "receive_hl7_adt",
        "receive_hl7_adt_hl7_adt_post",
        ("admin", "clinician"),
        None,
        None,
        ("HL7 Integration",),
        ["200"],
    ),
    (
        "POST",
        "/providers",
        "create_provider_endpoint",
        "create_provider_endpoint_providers_post",
        ("admin",),
        "Provider",
        201,
        ("Providers",),
        ["201"],
    ),
    (
        "GET",
        "/providers",
        "list_providers_endpoint",
        "list_providers_endpoint_providers_get",
        ROLES,
        "list[Provider]",
        None,
        ("Providers",),
        ["200"],
    ),
    (
        "GET",
        "/providers/{provider_id}",
        "get_provider_endpoint",
        "get_provider_endpoint_providers__provider_id__get",
        ROLES,
        "Provider",
        None,
        ("Providers",),
        ["200"],
    ),
    (
        "PUT",
        "/providers/{provider_id}",
        "update_provider_endpoint",
        "update_provider_endpoint_providers__provider_id__put",
        ("admin",),
        "Provider",
        None,
        ("Providers",),
        ["200"],
    ),
    (
        "DELETE",
        "/providers/{provider_id}",
        "delete_provider_endpoint",
        "delete_provider_endpoint_providers__provider_id__delete",
        ("admin",),
        None,
        204,
        ("Providers",),
        ["204"],
    ),
    (
        "POST",
        "/appointments",
        "create_appointment_endpoint",
        "create_appointment_endpoint_appointments_post",
        ("admin", "scheduler"),
        "Appointment",
        201,
        ("Appointments",),
        ["201"],
    ),
    (
        "GET",
        "/appointments",
        "list_appointments_endpoint",
        "list_appointments_endpoint_appointments_get",
        ("admin", "clinician", "scheduler"),
        "list[Appointment]",
        None,
        ("Appointments",),
        ["200"],
    ),
    (
        "GET",
        "/appointments/{appointment_id}",
        "get_appointment_endpoint",
        "get_appointment_endpoint_appointments__appointment_id__get",
        ("admin", "clinician", "scheduler"),
        "Appointment",
        None,
        ("Appointments",),
        ["200"],
    ),
    (
        "PUT",
        "/appointments/{appointment_id}",
        "update_appointment_endpoint",
        "update_appointment_endpoint_appointments__appointment_id__put",
        ("admin", "scheduler"),
        "Appointment",
        None,
        ("Appointments",),
        ["200"],
    ),
    (
        "PATCH",
        "/appointments/{appointment_id}/cancel",
        "cancel_appointment_endpoint",
        "cancel_appointment_endpoint_appointments__appointment_id__cancel_patch",
        ("admin", "scheduler"),
        "Appointment",
        None,
        ("Appointments",),
        ["200"],
    ),
    (
        "GET",
        "/auth/me",
        "read_current_user",
        "read_current_user_auth_me_get",
        AUTHENTICATED,
        None,
        None,
        ("Authentication",),
        ["200"],
    ),
    (
        "POST",
        "/auth/login",
        "login",
        "login_auth_login_post",
        PUBLIC,
        None,
        None,
        ("Authentication",),
        ["200"],
    ),
    (
        "GET",
        "/auth/admin-check",
        "admin_check",
        "admin_check_auth_admin_check_get",
        ("admin",),
        None,
        None,
        ("Authentication",),
        ["200"],
    ),
    (
        "GET",
        "/fhir/Practitioner/{practitioner_id}",
        "get_fhir_practitioner",
        "get_fhir_practitioner_fhir_Practitioner__practitioner_id__get",
        ROLES,
        None,
        None,
        (),
        ["200"],
    ),
    (
        "GET",
        "/fhir/Practitioner",
        "search_fhir_practitioners",
        "search_fhir_practitioners_fhir_Practitioner_get",
        ROLES,
        None,
        None,
        (),
        ["200"],
    ),
    (
        "GET",
        "/fhir/PractitionerRole/{role_id}",
        "get_fhir_practitioner_role",
        "get_fhir_practitioner_role_fhir_PractitionerRole__role_id__get",
        ROLES,
        None,
        None,
        (),
        ["200"],
    ),
    (
        "GET",
        "/fhir/PractitionerRole",
        "search_fhir_practitioner_roles",
        "search_fhir_practitioner_roles_fhir_PractitionerRole_get",
        ROLES,
        None,
        None,
        (),
        ["200"],
    ),
]

# Routes FastAPI adds itself (documentation), kept separate from the API.
FRAMEWORK_ROUTES = {
    ("/openapi.json", "openapi"),
    ("/docs", "swagger_ui_html"),
    ("/docs/oauth2-redirect", "swagger_ui_redirect"),
    ("/redoc", "redoc_html"),
}

# A request per operation that reaches the handler only for authorized callers
# and cannot write anything: unknown IDs, empty bodies that fail validation,
# or a missing HL7 content type. Expected status for an authorized caller.
PROBES = {
    ("GET", "/"): ("/", {}, 200),
    ("GET", "/health"): ("/health", {}, 200),
    ("GET", "/audit-logs"): ("/audit-logs", {}, 200),
    ("GET", "/patients"): ("/patients", {}, 200),
    ("GET", "/patients/{patient_id}"): ("/patients/999999", {}, 404),
    ("POST", "/patients"): ("/patients", {"json": {}}, 422),
    ("DELETE", "/patients/{patient_id}"): ("/patients/999999", {}, 404),
    ("PUT", "/patients/{patient_id}"): ("/patients/999999", {"json": {}}, 422),
    ("GET", "/fhir/metadata"): ("/fhir/metadata", {}, 200),
    ("GET", "/fhir/Patient/{patient_id}"): ("/fhir/Patient/999999", {}, 404),
    ("GET", "/fhir/Patient"): ("/fhir/Patient", {}, 200),
    ("POST", "/hl7/adt"): ("/hl7/adt", {}, 415),
    ("POST", "/providers"): ("/providers", {"json": {}}, 422),
    ("GET", "/providers"): ("/providers", {}, 200),
    ("GET", "/providers/{provider_id}"): ("/providers/999999", {}, 404),
    ("PUT", "/providers/{provider_id}"): ("/providers/999999", {"json": {}}, 422),
    ("DELETE", "/providers/{provider_id}"): ("/providers/999999", {}, 404),
    ("POST", "/appointments"): ("/appointments", {"json": {}}, 422),
    ("GET", "/appointments"): ("/appointments", {}, 200),
    ("GET", "/appointments/{appointment_id}"): ("/appointments/999999", {}, 404),
    ("PUT", "/appointments/{appointment_id}"): ("/appointments/999999", {"json": {}}, 422),
    ("PATCH", "/appointments/{appointment_id}/cancel"): ("/appointments/999999/cancel", {}, 404),
    ("GET", "/auth/me"): ("/auth/me", {}, 200),
    ("POST", "/auth/login"): ("/auth/login", {}, 422),
    ("GET", "/auth/admin-check"): ("/auth/admin-check", {}, 200),
    ("GET", "/fhir/Practitioner/{practitioner_id}"): ("/fhir/Practitioner/999999", {}, 404),
    ("GET", "/fhir/Practitioner"): ("/fhir/Practitioner", {}, 200),
    ("GET", "/fhir/PractitionerRole/{role_id}"): ("/fhir/PractitionerRole/999999", {}, 404),
    ("GET", "/fhir/PractitionerRole"): ("/fhir/PractitionerRole", {}, 200),
}

client = TestClient(app)


def _effective_routes():
    """
    Every route the application serves. FastAPI includes routers lazily, so
    app.routes holds one entry per included router; iter_route_contexts
    expands them, and works the same for routes defined directly on the app.
    """
    return list(iter_route_contexts(app.routes))


def _api_routes():
    return {
        (method, context.path): context.original_route
        for context in _effective_routes()
        if isinstance(context.original_route, APIRoute)
        for method in context.methods
    }


def _model_name(model):
    if model is None:
        return None
    if get_origin(model) is list:
        return f"list[{get_args(model)[0].__name__}]"
    return model.__name__


def _dependency_calls(route):
    stack, calls = list(route.dependant.dependencies), []
    while stack:
        dependant = stack.pop()
        calls.append(dependant.call)
        stack.extend(dependant.dependencies)
    return calls


def _contract_id(entry):
    return f"{entry[0]} {entry[1]}"


# --- static contract -----------------------------------------------------------


def test_contract_table_is_complete_and_unique():
    keys = [(m, p) for m, p, *_ in CONTRACT]
    assert len(keys) == 29
    assert len(set(keys)) == 29
    assert set(keys) == set(PROBES)


def test_exactly_the_contracted_operations_exist():
    assert set(_api_routes()) == {(m, p) for m, p, *_ in CONTRACT}


def test_framework_routes_are_present_and_separate():
    routes = _effective_routes()
    framework = {(c.path, c.name) for c in routes if not isinstance(c.original_route, APIRoute)}
    assert framework == FRAMEWORK_ROUTES
    assert len(routes) == 29 + 4


@pytest.mark.parametrize("entry", CONTRACT, ids=_contract_id)
def test_route_attributes_match_the_contract(entry):
    method, path, name, _, _, response_model, status_code, tags, _ = entry
    route = _api_routes()[(method, path)]

    assert route.name == name
    assert route.endpoint.__name__ == name
    assert _model_name(route.response_model) == response_model
    assert route.status_code == status_code
    assert tuple(route.tags) == tags
    assert route.include_in_schema is True


@pytest.mark.parametrize("entry", CONTRACT, ids=_contract_id)
def test_openapi_operation_matches_the_contract(entry):
    method, path, _, operation_id, _, _, _, tags, success_codes = entry
    operation = app.openapi()["paths"][path][method.lower()]

    assert operation["operationId"] == operation_id
    assert tuple(operation.get("tags", ())) == tags
    assert sorted(code for code in operation["responses"] if code.startswith("2")) == success_codes


def test_openapi_has_exactly_the_contracted_operations():
    paths = app.openapi()["paths"]
    documented = {(m.upper(), p) for p, item in paths.items() for m in item}
    assert documented == {(m, p) for m, p, *_ in CONTRACT}


@pytest.mark.parametrize("entry", CONTRACT, ids=_contract_id)
def test_authentication_dependency_is_declared_exactly_where_required(entry):
    method, path, _, _, access, *_ = entry
    calls = _dependency_calls(_api_routes()[(method, path)])
    uses_current_user = auth_dependencies.get_current_user in calls

    # The dependency must be the very object tests override, so that
    # app.dependency_overrides[get_current_user] keeps working.
    assert uses_current_user is (access != PUBLIC)
    named = [c for c in calls if getattr(c, "__name__", "") == "get_current_user"]
    assert all(c is auth_dependencies.get_current_user for c in named)


def test_fhir_route_names_resolve_for_fullurl_generation():
    assert app.url_path_for("get_fhir_patient", patient_id=7) == "/fhir/Patient/7"
    assert app.url_path_for("get_fhir_practitioner", practitioner_id=7) == "/fhir/Practitioner/7"
    assert app.url_path_for("get_fhir_practitioner_role", role_id=7) == "/fhir/PractitionerRole/7"


def test_fhir_validation_handler_is_registered_on_the_application():
    handler = app.exception_handlers[RequestValidationError]
    assert handler.__name__ == "fhir_validation_exception_handler"
    assert handler.__module__ == "main"


# --- request routing, through the application's real dispatch -------------------
#
# Each request goes through the full FastAPI/Starlette stack; FastAPI records the
# route it selected in scope["route"]. Protected routes stop at authentication
# (no handler code runs) because these requests carry no credentials.

ROUTING_CASES = [
    *[(method, PROBES[(method, path)][0], name) for method, path, name, *_ in CONTRACT],
    # Non-numeric IDs still reach the parameterised route (and then fail validation).
    ("GET", "/patients/not-a-number", "get_patient"),
    ("GET", "/fhir/Patient/abc", "get_fhir_patient"),
    # Static FHIR paths are not captured by a parameterised route.
    ("GET", "/fhir/metadata", "get_fhir_metadata"),
    ("GET", "/fhir/Practitioner", "search_fhir_practitioners"),
    # The cancel sub-resource is distinct from the appointment item route.
    ("PATCH", "/appointments/12/cancel", "cancel_appointment_endpoint"),
    ("GET", "/appointments/12", "get_appointment_endpoint"),
]


def _dispatch(method, path):
    """(status code, name of the route FastAPI selected) for a real request."""
    selected = {}

    async def recording_app(scope, receive, send):
        await app(scope, receive, send)
        selected["route"] = scope.get("route")

    response = TestClient(recording_app).request(method, path)
    return response.status_code, getattr(selected.get("route"), "name", None)


@pytest.mark.parametrize(("method", "path", "expected"), ROUTING_CASES)
def test_requests_route_to_the_expected_handler(method, path, expected):
    _, selected = _dispatch(method, path)
    assert selected == expected


@pytest.mark.parametrize(
    ("method", "path", "route_for_path"),
    [
        ("GET", "/appointments/12/cancel", "cancel_appointment_endpoint"),
        ("DELETE", "/fhir/metadata", "get_fhir_metadata"),
        ("POST", "/health", "health_check"),
    ],
)
def test_wrong_methods_match_the_path_but_are_not_allowed(method, path, route_for_path):
    status, selected = _dispatch(method, path)
    assert status == 405
    assert selected == route_for_path


# --- behavioural authorization -------------------------------------------------------


@pytest.fixture
def as_role():
    original = app.dependency_overrides.copy()

    def use(role):
        app.dependency_overrides[auth_dependencies.get_current_user] = lambda: {
            "id": None,
            "username": f"contract_{role}",
            "email": f"contract-{role}@example.com",
            "role": role,
            "is_active": True,
        }

    yield use
    app.dependency_overrides.clear()
    app.dependency_overrides.update(original)


def _call(method, path):
    url, kwargs, _ = PROBES[(method, path)]
    return client.request(method, url, **kwargs)


@pytest.mark.parametrize("entry", CONTRACT, ids=_contract_id)
def test_unauthenticated_requests(entry):
    method, path, _, _, access, *_ = entry
    response = _call(method, path)

    if access == PUBLIC:
        assert response.status_code == PROBES[(method, path)][2]
    else:
        assert response.status_code == 401
        assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("entry", [e for e in CONTRACT if e[4] != PUBLIC], ids=_contract_id)
def test_each_role_is_allowed_or_denied_as_contracted(entry, role, as_role):
    method, path, _, _, access, *_ = entry
    as_role(role)
    response = _call(method, path)

    allowed = access == AUTHENTICATED or role in access
    if allowed:
        assert response.status_code == PROBES[(method, path)][2], response.text
    else:
        assert response.status_code == 403
        assert response.json() == {"detail": "Insufficient permissions"}
