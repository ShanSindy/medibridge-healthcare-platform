"""
FHIR Practitioner and PractitionerRole against real provider rows.

Providers are inserted into the disposable database and read back through the
real FHIR routes. Expected resources are written out literally rather than
built with the mapping functions under test. Authentication is not under test:
the current user is overridden with a synthetic viewer (providers:read).
Invalid _count/_offset values are already covered by
test_fhir_practitioner_search_api.py and test_fhir_practitioner_role_search_api.py.
"""

import psycopg
import pytest
from fastapi.testclient import TestClient

import main
from auth_dependencies import get_current_user
from tests import db_safety

FHIR_JSON = "application/fhir+json"
BASE = "http://testserver"


@pytest.fixture
def providers(disposable_database):
    conn = psycopg.connect(
        **disposable_database.config.connect_kwargs(disposable_database.name), autocommit=True
    )
    try:
        found, role_oid = db_safety.fetch_identity(conn, disposable_database.name)
        db_safety.check_identity(
            session_name=disposable_database.name,
            created=disposable_database.created,
            found=found,
            current_role_oid=role_oid,
            require_marker=True,
        )
        rows = [
            ("Ava", "Linden", "Cardiology", "ava.linden@example.org", "555-0101"),
            ("Ben", "Lindqvist", "Cardiac Surgery", None, None),
            ("Cora", "Marsh", "Neurology", "cora.marsh@example.org", None),
        ]
        ids = [
            conn.execute(
                "INSERT INTO providers (first_name, last_name, specialty, email, phone) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                row,
            ).fetchone()[0]
            for row in rows
        ]
    finally:
        conn.close()
    return dict(zip(("linden", "lindqvist", "marsh"), ids, strict=True))


@pytest.fixture
def client():
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_current_user] = lambda: {
        "id": None,
        "username": "synthetic.viewer",
        "email": "viewer@example.org",
        "role": "viewer",
        "is_active": True,
    }
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


def fhir_get(client, url):
    response = client.get(url)
    assert response.headers["content-type"] == FHIR_JSON
    return response


def entry_ids(bundle):
    return [entry["resource"]["id"] for entry in bundle["entry"]]


def links(bundle):
    return {link["relation"]: link["url"] for link in bundle["link"]}


# --- Practitioner ------------------------------------------------------------------------


def test_practitioner_read_maps_the_stored_provider(client, providers):
    linden, lindqvist = providers["linden"], providers["lindqvist"]

    with_contacts = fhir_get(client, f"/fhir/Practitioner/{linden}")
    without_contacts = fhir_get(client, f"/fhir/Practitioner/{lindqvist}")

    assert with_contacts.status_code == 200
    assert with_contacts.json() == {
        "resourceType": "Practitioner",
        "id": str(linden),
        "name": [{"use": "official", "family": "Linden", "given": ["Ava"]}],
        "telecom": [
            {"system": "email", "value": "ava.linden@example.org"},
            {"system": "phone", "value": "555-0101"},
        ],
    }
    assert without_contacts.json() == {
        "resourceType": "Practitioner",
        "id": str(lindqvist),
        "name": [{"use": "official", "family": "Lindqvist", "given": ["Ben"]}],
    }


def test_practitioner_search_filters_and_pages(client, providers):
    linden, lindqvist, marsh = providers["linden"], providers["lindqvist"], providers["marsh"]

    by_family = fhir_get(client, "/fhir/Practitioner?family=lind").json()
    assert (by_family["resourceType"], by_family["type"], by_family["total"]) == (
        "Bundle",
        "searchset",
        2,
    )
    assert entry_ids(by_family) == [str(linden), str(lindqvist)]
    assert by_family["entry"][0]["fullUrl"] == f"{BASE}/fhir/Practitioner/{linden}"

    by_given = fhir_get(client, "/fhir/Practitioner?given=Cora").json()
    assert (by_given["total"], entry_ids(by_given)) == (1, [str(marsh)])

    first = fhir_get(client, "/fhir/Practitioner?family=lind&_count=1").json()
    second = fhir_get(client, "/fhir/Practitioner?family=lind&_count=1&_offset=1").json()
    assert (first["total"], entry_ids(first)) == (2, [str(linden)])
    assert links(first) == {
        "self": "/fhir/Practitioner?family=lind&_count=1&_offset=0",
        "next": "/fhir/Practitioner?family=lind&_count=1&_offset=1",
    }
    assert (second["total"], entry_ids(second)) == (2, [str(lindqvist)])
    assert links(second) == {
        "self": "/fhir/Practitioner?family=lind&_count=1&_offset=1",
        "previous": "/fhir/Practitioner?family=lind&_count=1&_offset=0",
    }

    past_end = fhir_get(client, "/fhir/Practitioner?family=lind&_offset=5").json()
    assert (past_end["total"], past_end["entry"]) == (2, [])
    empty = fhir_get(client, "/fhir/Practitioner?family=Nobody").json()
    assert (empty["total"], empty["entry"], links(empty)) == (
        0,
        [],
        {"self": "/fhir/Practitioner?family=Nobody&_count=10&_offset=0"},
    )


def test_unknown_practitioner_returns_not_found_operation_outcome(client, providers):
    missing = max(providers.values()) + 1000
    response = fhir_get(client, f"/fhir/Practitioner/{missing}")

    assert response.status_code == 404
    assert response.json() == {
        "resourceType": "OperationOutcome",
        "issue": [
            {"severity": "error", "code": "not-found", "diagnostics": "Practitioner not found"}
        ],
    }


# --- PractitionerRole --------------------------------------------------------------------


def test_practitioner_role_read_maps_the_stored_provider(client, providers):
    linden = providers["linden"]
    response = fhir_get(client, f"/fhir/PractitionerRole/{linden}")

    assert response.status_code == 200
    assert response.json() == {
        "resourceType": "PractitionerRole",
        "id": str(linden),
        "practitioner": {"reference": f"Practitioner/{linden}"},
        "specialty": [{"text": "Cardiology"}],
    }


def test_practitioner_role_search_filters_and_pages(client, providers):
    linden, lindqvist = providers["linden"], providers["lindqvist"]

    cardiac = fhir_get(client, "/fhir/PractitionerRole?specialty=card").json()
    assert (cardiac["type"], cardiac["total"], entry_ids(cardiac)) == (
        "searchset",
        2,
        [str(linden), str(lindqvist)],
    )
    assert cardiac["entry"][1]["fullUrl"] == f"{BASE}/fhir/PractitionerRole/{lindqvist}"
    assert cardiac["entry"][1]["resource"]["specialty"] == [{"text": "Cardiac Surgery"}]

    page = fhir_get(client, "/fhir/PractitionerRole?specialty=card&_count=1&_offset=1").json()
    assert (page["total"], entry_ids(page)) == (2, [str(lindqvist)])
    assert links(page) == {
        "self": "/fhir/PractitionerRole?specialty=card&_count=1&_offset=1",
        "previous": "/fhir/PractitionerRole?specialty=card&_count=1&_offset=0",
    }

    empty = fhir_get(client, "/fhir/PractitionerRole?specialty=Orthopedics").json()
    assert (empty["total"], empty["entry"]) == (0, [])


def test_unknown_practitioner_role_returns_not_found_operation_outcome(client, providers):
    missing = max(providers.values()) + 1000
    response = fhir_get(client, f"/fhir/PractitionerRole/{missing}")

    assert response.status_code == 404
    assert response.json()["resourceType"] == "OperationOutcome"
    assert response.json()["issue"] == [
        {"severity": "error", "code": "not-found", "diagnostics": "PractitionerRole not found"}
    ]
