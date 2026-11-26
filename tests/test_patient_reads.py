"""
Patient reads on the shared SQLAlchemy engine, against this session's disposable database.

Two kinds of test, kept apart:

- Contract tests use only what existed before Phase 2 (the REST and FHIR routes and the
  identifier helpers in database.py) and fixed expected results written from the
  synthetic data below, never computed by the code under test. They were run against the
  previous implementation before the change.
- Engine tests (marked in their names and sections) check patient_service and the use of
  the shared engine: read-only transactions, the timeout policy, connection release,
  and which reads share a transaction. They cannot pass on the previous implementation.

Patient writes and HL7 registration stay on database.get_connection(); the mixed-path
tests use them unchanged.
"""

import threading
import time
from contextlib import contextmanager
from datetime import date

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import column, event, table
from sqlalchemy import exc as sa_exc

import database
import db_engine
import main
from auth_dependencies import get_current_user
from database import get_identifiers_for_patients, get_patient_identifiers
from tests.pg_helpers import verified_connection, verified_engine

FIELDS = ["id", "first_name", "last_name", "date_of_birth", "gender"]
SYS_A, SYS_B = "urn:example:mrn-a", "urn:example:mrn-b"

# Synthetic patients, inserted in this order (so ids ascend in this order). Chosen for
# the search edge cases: case, prefixes, the LIKE wildcards % and _, the escape
# character, non-ASCII letters, and a patient without identifiers.
SYNTHETIC = {
    "n1": ("Joanna", "Smith", date(1980, 1, 31), "female"),
    "n2": ("john", "smithson", date(1975, 12, 1), "male"),
    "n3": ("Jo", "Smyth", date(2000, 2, 29), "other"),
    "n4": ("Percy", "100%_Synthetic", date(1999, 9, 9), "unknown"),
    "n5": ("Una", "O_Neil", date(1988, 8, 8), "male"),
    "n6": ("Bea", "Path\\Name", date(1977, 7, 7), "female"),
    "n7": ("Zoë", "Ångström", date(1966, 6, 6), "other"),
    "n8": ("Noor", "Plain", date(1950, 5, 5), "male"),
}
ALL = list(SYNTHETIC)

# Identifiers, inserted in this order (so their ids ascend in this order). n1 carries the
# same value under two systems: an identifier search must still return n1 once.
IDENTIFIERS = [
    ("n1", SYS_B, "SHARED-7"),
    ("n1", SYS_A, "MRN-1"),
    ("n1", SYS_A, "SHARED-7"),
    ("n2", SYS_A, "MRN-2"),
    ("n2", "urn:example:pipe", "a|b"),
    ("n3", "urn:x|y", "V3"),
]


# --- fixtures ----------------------------------------------------------------------------


@pytest.fixture
def direct(disposable_database):
    with verified_connection(disposable_database) as conn:
        yield conn


@pytest.fixture
def seeded(direct):
    """The synthetic patients and identifiers; returns {name: id}."""
    ids = {}
    for name, (first, last, born, gender) in SYNTHETIC.items():
        ids[name] = direct.execute(
            "INSERT INTO patients (first_name, last_name, date_of_birth, gender) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (first, last, born, gender),
        ).fetchone()[0]
    for name, system, value in IDENTIFIERS:
        direct.execute(
            "INSERT INTO patient_identifiers (patient_id, system, value) VALUES (%s, %s, %s)",
            (ids[name], system, value),
        )
    # New versions of the first patient and first identifier are stored after the
    # others, so storage order differs from id order and only ORDER BY gives the
    # documented order.
    direct.execute("UPDATE patients SET gender = gender WHERE id = %s", (ids["n1"],))
    direct.execute(
        "UPDATE patient_identifiers SET value = value WHERE patient_id = %s AND system = %s "
        "AND value = 'SHARED-7'",
        (ids["n1"], SYS_B),
    )
    return ids


def record(ids, name) -> dict:
    first, last, born, gender = SYNTHETIC[name]
    return {
        "id": ids[name],
        "first_name": first,
        "last_name": last,
        "date_of_birth": born,
        "gender": gender,
    }


def identifiers_of(name) -> list[dict]:
    return [{"system": s, "value": v} for n, s, v in IDENTIFIERS if n == name]


def as_json(ids, name) -> dict:
    return {**record(ids, name), "date_of_birth": SYNTHETIC[name][2].isoformat()}


def fhir_patient(ids, name) -> dict:
    first, last, born, gender = SYNTHETIC[name]
    resource = {
        "resourceType": "Patient",
        "id": str(ids[name]),
        "name": [{"use": "official", "family": last, "given": [first]}],
        "gender": gender,
        "birthDate": born.isoformat(),
    }
    if identifiers_of(name):
        resource["identifier"] = identifiers_of(name)
    return resource


def user(role):
    return {
        "id": None,
        "username": f"synthetic.{role}",
        "email": f"{role}@example.org",
        "role": role,
        "is_active": True,
    }


@contextmanager
def signed_in(role):
    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_current_user] = lambda: user(role)
    try:
        yield TestClient(main.app, raise_server_exceptions=False)
    finally:
        main.app.dependency_overrides.clear()
        main.app.dependency_overrides.update(previous)


@pytest.fixture
def client():
    with signed_in("admin") as test_client:
        yield test_client


def names_in_bundle(bundle, ids) -> list[str]:
    by_id = {str(value): key for key, value in ids.items()}
    return [by_id[entry["resource"]["id"]] for entry in bundle["entry"]]


# --- contract: REST list and lookup --------------------------------------------------------


def test_rest_list_of_an_empty_table_is_empty(client, direct):
    response = client.get("/patients")

    assert (response.status_code, response.json()) == (200, [])


def test_rest_list_is_every_patient_by_id(client, direct, seeded):
    stored = [row[0] for row in direct.execute("SELECT id FROM patients").fetchall()]
    assert stored != sorted(stored)  # precondition: storage order is not id order

    response = client.get("/patients")

    assert response.status_code == 200
    assert response.json() == [as_json(seeded, name) for name in ALL]
    assert [list(item) for item in response.json()] == [FIELDS] * len(ALL)


def test_rest_lookup_returns_each_patient(client, seeded):
    for name in ALL:
        response = client.get(f"/patients/{seeded[name]}")
        assert (response.status_code, response.json()) == (200, as_json(seeded, name))
        assert list(response.json()) == FIELDS


@pytest.mark.parametrize("missing", [0, -1, 2_147_483_647, 2_147_483_648, 10**20])
def test_rest_lookup_of_a_missing_patient_is_404(client, seeded, missing):
    response = client.get(f"/patients/{missing}")

    assert (response.status_code, response.json()) == (404, {"detail": "Patient not found"})


def test_rest_lookup_validates_the_id(client, seeded):
    assert client.get("/patients/abc").status_code == 422


# --- contract: FHIR lookup -----------------------------------------------------------------


def test_fhir_lookup_builds_each_patient_with_its_identifiers_in_order(client, seeded):
    for name in ALL:
        response = client.get(f"/fhir/Patient/{seeded[name]}")
        assert (response.status_code, response.json()) == (200, fhir_patient(seeded, name))


@pytest.mark.parametrize("missing", [0, -1, 2_147_483_648, 10**20])
def test_fhir_lookup_of_a_missing_patient_is_an_operation_outcome(client, seeded, missing):
    response = client.get(f"/fhir/Patient/{missing}")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/fhir+json")
    assert response.json() == {
        "resourceType": "OperationOutcome",
        "issue": [{"severity": "error", "code": "not-found", "diagnostics": "Patient not found"}],
    }


# --- contract: FHIR search -----------------------------------------------------------------

SEARCHES = {
    "no filter": ({}, ALL),
    "family, case-insensitive prefix": ({"family": "smith"}, ["n1", "n2"]),
    "family, upper case": ({"family": "SMI"}, ["n1", "n2"]),
    "family, shorter prefix": ({"family": "Sm"}, ["n1", "n2", "n3"]),
    "family, not a substring match": ({"family": "mith"}, []),
    "family, literal percent": ({"family": "100%"}, ["n4"]),
    "family, percent is not a wildcard": ({"family": "1%"}, []),
    "family, literal underscore": ({"family": "O_"}, ["n5"]),
    "family, underscore is not a wildcard": ({"family": "_"}, []),
    "family, literal backslash": ({"family": "Path\\"}, ["n6"]),
    "family, backslash then letter": ({"family": "Path\\N"}, ["n6"]),
    "family, non-ASCII": ({"family": "Ång"}, ["n7"]),
    "family, empty matches all": ({"family": ""}, ALL),
    "family, leading space is kept": ({"family": " Smith"}, []),
    "given, prefix": ({"given": "jo"}, ["n1", "n2", "n3"]),
    "given, upper case": ({"given": "JOHN"}, ["n2"]),
    "family and given": ({"family": "smith", "given": "jo"}, ["n1", "n2"]),
    "family and given, narrower": ({"family": "smith", "given": "joa"}, ["n1"]),
    "family and given, disjoint": ({"family": "smyth", "given": "joa"}, []),
    "identifier, value under two systems": ({"identifier": "SHARED-7"}, ["n1"]),
    "identifier, system and value": ({"identifier": f"{SYS_B}|SHARED-7"}, ["n1"]),
    "identifier, other system and value": ({"identifier": f"{SYS_A}|MRN-2"}, ["n2"]),
    "identifier, wrong system": ({"identifier": f"{SYS_B}|MRN-2"}, []),
    "identifier, value only": ({"identifier": "MRN-2"}, ["n2"]),
    "identifier, case-sensitive": ({"identifier": "mrn-2"}, []),
    "identifier, exact not prefix": ({"identifier": "MRN"}, []),
    "identifier, first bar splits": ({"identifier": "a|b"}, []),
    "identifier, value containing a bar": ({"identifier": "urn:example:pipe|a|b"}, ["n2"]),
    "identifier, system containing a bar": ({"identifier": "urn:x|y|V3"}, []),
    "identifier, empty": ({"identifier": ""}, []),
    "identifier, bar only": ({"identifier": "|"}, []),
    "identifier, empty system": ({"identifier": "|MRN-2"}, []),
    "identifier, empty value": ({"identifier": f"{SYS_A}|"}, []),
    "identifier and family": ({"identifier": "SHARED-7", "family": "Smith"}, ["n1"]),
    "identifier and other family": ({"identifier": "SHARED-7", "family": "Smyth"}, []),
    "identifier and given": ({"identifier": "MRN-1", "given": "jo"}, ["n1"]),
    "quotes and SQL are plain values": ({"family": "'; DROP TABLE patients; --"}, []),
}


@pytest.mark.parametrize(("filters", "expected"), SEARCHES.values(), ids=SEARCHES.keys())
def test_fhir_search_matches_fixed_expectations(client, seeded, filters, expected):
    response = client.get("/fhir/Patient", params={**filters, "_count": 100})

    assert response.status_code == 200
    bundle = response.json()
    assert names_in_bundle(bundle, seeded) == expected
    assert bundle["total"] == len(expected)


def test_fhir_search_bundle_is_complete(client, seeded):
    response = client.get("/fhir/Patient", params={"family": "sm", "_count": 1, "_offset": 1})

    assert response.status_code == 200
    assert response.json() == {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": 3,
        "link": [
            {"relation": "self", "url": "/fhir/Patient?family=sm&_count=1&_offset=1"},
            {"relation": "next", "url": "/fhir/Patient?family=sm&_count=1&_offset=2"},
            {"relation": "previous", "url": "/fhir/Patient?family=sm&_count=1&_offset=0"},
        ],
        "entry": [
            {
                "fullUrl": f"http://testserver/fhir/Patient/{seeded['n2']}",
                "resource": fhir_patient(seeded, "n2"),
            }
        ],
    }


def test_fhir_search_links_keep_every_filter(client, seeded):
    response = client.get(
        "/fhir/Patient",
        params={"family": "Smith", "given": "Jo", "identifier": f"{SYS_A}|MRN-1", "_count": 5},
    )

    assert response.json()["link"] == [
        {
            "relation": "self",
            "url": "/fhir/Patient?family=Smith&given=Jo&identifier=urn%3Aexample%3Amrn-a"
            "%7CMRN-1&_count=5&_offset=0",
        }
    ]


PAGES = [
    (10, 0, ALL),
    (3, 0, ["n1", "n2", "n3"]),
    (3, 6, ["n7", "n8"]),
    (3, 8, []),
    (1, 7, ["n8"]),
    (100, 0, ALL),
    (5, 2_147_483_648, []),
]


@pytest.mark.parametrize(("count", "offset", "expected"), PAGES)
def test_fhir_search_pages_report_the_full_total(client, seeded, count, offset, expected):
    response = client.get("/fhir/Patient", params={"_count": count, "_offset": offset})

    assert response.status_code == 200
    bundle = response.json()
    assert names_in_bundle(bundle, seeded) == expected
    assert bundle["total"] == len(ALL)
    relations = [link["relation"] for link in bundle["link"]]
    assert ("next" in relations) is (offset + count < len(ALL))
    assert ("previous" in relations) is (offset > 0)


def test_fhir_search_of_an_empty_table(client, direct):
    response = client.get("/fhir/Patient")

    assert response.json() == {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": 0,
        "link": [{"relation": "self", "url": "/fhir/Patient?_count=10&_offset=0"}],
        "entry": [],
    }


@pytest.mark.parametrize(
    "params", [{"_count": 0}, {"_count": 101}, {"_offset": -1}, {"_count": "x"}]
)
def test_fhir_search_validation_is_an_operation_outcome(client, seeded, params):
    response = client.get("/fhir/Patient", params=params)

    assert response.status_code == 422
    assert response.json() == {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": "error",
                "code": "invalid",
                "diagnostics": "Invalid FHIR request parameters",
            }
        ],
    }


# --- contract: identifier helpers ----------------------------------------------------------


def test_identifiers_of_one_patient_in_id_order(seeded):
    result = get_patient_identifiers(seeded["n1"])

    assert type(result) is list
    assert [type(item) for item in result] == [dict] * 3
    assert result == identifiers_of("n1")
    assert [list(item) for item in result] == [["system", "value"]] * 3


def test_identifiers_of_a_patient_without_any(seeded):
    assert get_patient_identifiers(seeded["n8"]) == []
    assert get_patient_identifiers(0) == []


def test_identifiers_for_patients_are_grouped_in_request_order(seeded):
    requested = [seeded["n2"], seeded["n1"], seeded["n8"], 0]

    result = get_identifiers_for_patients(requested)

    assert type(result) is dict
    assert list(result) == requested  # every requested id, in request order
    assert result == {
        seeded["n2"]: identifiers_of("n2"),
        seeded["n1"]: identifiers_of("n1"),
        seeded["n8"]: [],
        0: [],
    }
    assert all(type(items) is list for items in result.values())
    assert all(type(item) is dict for items in result.values() for item in items)


def test_identifiers_for_repeated_patient_ids_are_not_repeated(seeded):
    result = get_identifiers_for_patients([seeded["n1"], seeded["n1"]])

    assert result == {seeded["n1"]: identifiers_of("n1")}


def test_identifiers_for_an_id_beyond_the_integer_range(seeded):
    assert get_identifiers_for_patients([2_147_483_648]) == {2_147_483_648: []}


def test_identifiers_for_no_patients_open_no_connection(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no connection should be opened")

    monkeypatch.setattr(database, "get_connection", forbidden)
    monkeypatch.setattr(database, "read_only_connection", forbidden, raising=False)
    monkeypatch.setattr(db_engine, "get_engine", forbidden)

    assert get_identifiers_for_patients([]) == {}


# --- contract: authorization ---------------------------------------------------------------

READ_ROUTES = ["/patients", "/patients/{n1}", "/fhir/Patient", "/fhir/Patient/{n1}"]


@pytest.mark.parametrize("role", ["admin", "clinician", "scheduler"])
@pytest.mark.parametrize("route", READ_ROUTES)
def test_roles_with_patients_read_get_the_patient(seeded, role, route):
    with signed_in(role) as test_client:
        response = test_client.get(route.format(**seeded))

    assert response.status_code == 200
    assert "Smith" in response.text


@pytest.mark.parametrize("route", READ_ROUTES)
def test_a_viewer_is_refused_without_patient_data(seeded, route):
    with signed_in("viewer") as test_client:
        response = test_client.get(route.format(**seeded))

    assert response.status_code == 403
    assert "Smith" not in response.text


@pytest.mark.parametrize("route", READ_ROUTES)
def test_an_anonymous_caller_is_refused_without_patient_data(seeded, route):
    response = TestClient(main.app).get(route.format(**seeded))

    assert response.status_code == 401
    assert "Smith" not in response.text


# --- contract: legacy writes and pooled reads ----------------------------------------------


HL7_REGISTRATION = (
    "MSH|^~\\&|SYN_READS|SYN_FAC|MEDIBRIDGE|MEDIBRIDGE|20311106120000||ADT^A04|SYN-READ-0001|P|2.5\r"
    "EVN|A04|20311106120000\r"
    "PID|1||SYN-READ-123^^^SYN_FAC^MR||Registered^Rowan||19900101|U"
)


def test_an_hl7_registration_is_visible_to_the_reads(client, direct):
    from hl7_service import HL7_MRN_SYSTEM, register_patient_from_hl7

    registered = register_patient_from_hl7(HL7_REGISTRATION)
    assert registered["status"] == "created"
    patient_id = registered["patient_id"]

    found = client.get("/fhir/Patient", params={"identifier": "SYN-READ-123"}).json()
    assert found["total"] == 1
    assert found["entry"][0]["resource"]["id"] == str(patient_id)
    assert found["entry"][0]["resource"]["identifier"] == [
        {"system": HL7_MRN_SYSTEM, "value": "SYN-READ-123"}
    ]
    assert client.get(f"/patients/{patient_id}").json()["last_name"] == "Registered"

    # A repeated registration is refused inside its own transaction and changes nothing.
    assert register_patient_from_hl7(HL7_REGISTRATION)["status"] != "created"
    assert client.get("/fhir/Patient").json()["total"] == 1


def test_rest_writes_are_visible_to_the_reads(client, direct):
    created = client.post(
        "/patients",
        json={
            "first_name": "Ivy",
            "last_name": "Synthetic",
            "date_of_birth": "1991-03-04",
            "gender": "female",
        },
    ).json()
    assert client.get(f"/patients/{created['id']}").json() == created

    client.put(
        f"/patients/{created['id']}",
        json={**{k: created[k] for k in FIELDS[1:]}, "last_name": "Synthetica"},
    )
    assert client.get("/fhir/Patient", params={"family": "Synthetica"}).json()["total"] == 1

    direct.execute(
        "INSERT INTO patient_identifiers (patient_id, system, value) VALUES (%s, %s, %s)",
        (created["id"], SYS_A, "MRN-IVY"),
    )
    found = client.get("/fhir/Patient", params={"identifier": "MRN-IVY"}).json()
    assert [entry["resource"]["id"] for entry in found["entry"]] == [str(created["id"])]

    client.delete(f"/patients/{created['id']}")
    assert client.get(f"/patients/{created['id']}").status_code == 404
    assert get_patient_identifiers(created["id"]) == []


def test_an_uncommitted_or_rolled_back_write_is_never_visible(client, disposable_database):
    with verified_connection(disposable_database) as other:
        other.autocommit = False
        other.execute(
            "INSERT INTO patients (first_name, last_name, date_of_birth, gender) "
            "VALUES ('Rolled', 'Back', '1990-01-01', 'other')"
        )
        assert client.get("/fhir/Patient", params={"family": "Back"}).json()["total"] == 0
        other.rollback()

    assert client.get("/patients").json() == []


def test_a_refused_delete_leaves_the_patient_readable(client, direct, seeded):
    provider = direct.execute(
        "INSERT INTO providers (first_name, last_name, specialty) "
        "VALUES ('Dana', 'Synthetica', 'Synthetic Medicine') RETURNING id"
    ).fetchone()[0]
    direct.execute(
        "INSERT INTO appointments (patient_id, provider_id, starts_at, ends_at) "
        "VALUES (%s, %s, '2030-01-01 09:00+00', '2030-01-01 09:30+00')",
        (seeded["n8"], provider),
    )

    assert client.delete(f"/patients/{seeded['n8']}").status_code == 409
    assert client.get(f"/patients/{seeded['n8']}").json() == as_json(seeded, "n8")


# --- engine: patient_service ---------------------------------------------------------------


@pytest.fixture
def service():
    import patient_service

    return patient_service


def test_engine_service_list_and_lookup_return_plain_records(service, direct, seeded):
    assert service.get_all_patients() == [record(seeded, name) for name in ALL]
    result = service.get_all_patients()
    assert type(result) is list
    assert [type(item) for item in result] == [dict] * len(ALL)
    assert [type(item["date_of_birth"]) for item in result] == [date] * len(ALL)

    found = service.get_patient_by_id(seeded["n6"])
    assert type(found) is dict
    assert found == record(seeded, "n6")
    assert list(found) == FIELDS
    assert service.get_patient_by_id(0) is None
    assert db_engine.get_engine().pool.checkedout() == 0


def test_engine_service_empty_list_is_a_plain_list(service, direct):
    result = service.get_all_patients()

    assert type(result) is list
    assert result == []


def test_engine_service_search_returns_a_plain_page_and_total(service, seeded):
    page, total = service.search_patients(family="sm", count=2, offset=1)

    assert type(page) is list
    assert [type(item) for item in page] == [dict] * 2
    assert page == [record(seeded, "n2"), record(seeded, "n3")]
    assert (type(total), total) == (int, 3)
    assert service.search_patients() == ([record(seeded, name) for name in ALL], len(ALL))


def transactions_on_the_engine(read):
    """Runs `read` and returns one list of (statement, read-only) per engine transaction."""
    engine = db_engine.get_engine()
    transactions, events = [], []
    state = {"open": False}

    # The engine's own begin listener (the timeout policy) runs its statement before
    # this one is called, so a transaction starts at whichever comes first.
    def start():
        if not state["open"]:
            transactions.append([])
            state["open"] = True

    def on_begin(conn):
        start()
        events.append("begin")

    def on_commit(conn):
        state["open"] = False
        events.append("commit")

    def on_rollback(conn):
        state["open"] = False
        events.append("rollback")

    def before_execute(conn, cursor, statement, parameters, context, executemany):
        start()
        transactions[-1].append((statement, conn.connection.driver_connection.read_only))

    listeners = [
        ("begin", on_begin),
        ("commit", on_commit),
        ("rollback", on_rollback),
        ("before_cursor_execute", before_execute),
    ]
    for name, listener in listeners:
        event.listen(engine, name, listener)
    try:
        result = read()
    finally:
        for name, listener in listeners:
            event.remove(engine, name, listener)
    assert "commit" not in events
    assert events == ["begin", "rollback"] * len(transactions)
    assert engine.pool.checkedout() == 0
    return result, transactions


def assert_read_only_with_timeouts(transaction, statements):
    assert "'statement_timeout'" in transaction[0][0]  # the timeout policy runs first
    assert sum("set_config" in statement for statement, _ in transaction) == 1
    assert len(transaction) == 1 + statements
    assert all(statement.lstrip().startswith("SELECT") for statement, _ in transaction)
    assert all(read_only for _, read_only in transaction)


SERVICE_READS = {
    "list": (lambda s, ids: s.get_all_patients(), 1),
    "lookup": (lambda s, ids: s.get_patient_by_id(ids["n1"]), 1),
    "search (page and total together)": (
        lambda s, ids: s.search_patients(identifier="SHARED-7", count=1),
        2,
    ),
    "identifiers of one patient": (lambda s, ids: get_patient_identifiers(ids["n1"]), 1),
    "identifiers of several patients": (
        lambda s, ids: get_identifiers_for_patients([ids["n1"], ids["n2"]]),
        1,
    ),
}


@pytest.mark.parametrize(("read", "statements"), SERVICE_READS.values(), ids=SERVICE_READS)
def test_engine_each_read_is_one_read_only_transaction_with_the_timeouts(
    service, seeded, read, statements
):
    result, transactions = transactions_on_the_engine(lambda: read(service, seeded))

    assert result
    assert len(transactions) == 1
    assert_read_only_with_timeouts(transactions[0], statements)


def test_engine_fhir_lookup_reads_patient_and_identifiers_in_separate_transactions(client, seeded):
    response, transactions = transactions_on_the_engine(
        lambda: client.get(f"/fhir/Patient/{seeded['n1']}")
    )

    assert response.json() == fhir_patient(seeded, "n1")
    assert len(transactions) == 2
    assert "FROM patients" in transactions[0][1][0]
    assert "FROM patient_identifiers" in transactions[1][1][0]
    for transaction in transactions:
        assert_read_only_with_timeouts(transaction, 1)


def test_engine_fhir_search_reads_page_and_total_together_then_identifiers(client, seeded):
    response, transactions = transactions_on_the_engine(
        lambda: client.get("/fhir/Patient", params={"family": "sm"})
    )

    assert response.json()["total"] == 3
    assert len(transactions) == 2
    page_and_total, identifiers = transactions
    assert_read_only_with_timeouts(page_and_total, 2)
    assert "count(" in page_and_total[2][0].lower()
    assert_read_only_with_timeouts(identifiers, 1)
    assert "FROM patient_identifiers" in identifiers[1][0]


def test_engine_an_empty_search_reads_no_identifiers(client, seeded):
    response, transactions = transactions_on_the_engine(
        lambda: client.get("/fhir/Patient", params={"family": "nobody"})
    )

    assert response.json()["total"] == 0
    assert len(transactions) == 1  # page and total; no identifier read for an empty page


def test_engine_rest_reads_use_the_engine(client, seeded):
    for url in ("/patients", f"/patients/{seeded['n1']}"):
        response, transactions = transactions_on_the_engine(lambda url=url: client.get(url))
        assert response.status_code == 200
        assert len(transactions) == 1


@pytest.mark.parametrize(("read", "statements"), SERVICE_READS.values(), ids=SERVICE_READS)
def test_engine_results_are_read_before_the_connection_is_released(
    monkeypatch, service, seeded, read, statements
):
    # Every result is closed as soon as read_only_connection() exits, as the results of
    # a server-side cursor would be. A read that fetched rows after the block would fail.
    opened = []
    real = db_engine.read_only_connection

    class RecordingConnection:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, *args, **kwargs):
            result = self._conn.execute(*args, **kwargs)
            opened.append(result)
            return result

    @contextmanager
    def closing_results():
        with real() as conn:
            yield RecordingConnection(conn)
        for result in opened:
            result.close()

    monkeypatch.setattr(service, "read_only_connection", closing_results)
    monkeypatch.setattr(database, "read_only_connection", closing_results)

    assert read(service, seeded)
    assert len(opened) == statements


def missing_table_of(module, attribute):
    original = getattr(module, attribute)
    return table(f"missing_{original.name}", *(column(c.name) for c in original.columns))


@pytest.mark.parametrize(("read", "statements"), SERVICE_READS.values(), ids=SERVICE_READS)
def test_engine_a_failing_read_propagates_and_returns_its_connection(
    monkeypatch, service, seeded, read, statements
):
    # Fault injection: the same columns from tables that do not exist.
    monkeypatch.setattr(service, "PATIENTS", missing_table_of(service, "PATIENTS"))
    monkeypatch.setattr(
        service, "PATIENT_IDENTIFIERS", missing_table_of(service, "PATIENT_IDENTIFIERS")
    )
    monkeypatch.setattr(
        database, "PATIENT_IDENTIFIERS", missing_table_of(database, "PATIENT_IDENTIFIERS")
    )

    with pytest.raises(sa_exc.ProgrammingError) as raised:
        read(service, seeded)

    assert isinstance(raised.value.orig, psycopg.errors.UndefinedTable)
    assert db_engine.get_engine().pool.checkedout() == 0
    monkeypatch.undo()
    assert service.get_patient_by_id(seeded["n1"]) == record(seeded, "n1")  # recovered


@pytest.mark.parametrize(
    "url", ["/patients", "/patients/{n1}", "/fhir/Patient/{n1}", "/fhir/Patient"]
)
def test_engine_a_failing_read_is_a_plain_500_without_details(
    monkeypatch, client, service, seeded, url
):
    monkeypatch.setattr(service, "PATIENTS", missing_table_of(service, "PATIENTS"))

    response = client.get(url.format(**seeded))

    assert (response.status_code, response.text) == (500, "Internal Server Error")


@pytest.mark.parametrize("url", ["/fhir/Patient/{n1}", "/fhir/Patient"])
def test_engine_a_failing_identifier_read_is_a_plain_500_without_details(
    monkeypatch, client, seeded, url
):
    monkeypatch.setattr(
        database, "PATIENT_IDENTIFIERS", missing_table_of(database, "PATIENT_IDENTIFIERS")
    )

    response = client.get(url.format(**seeded))

    assert (response.status_code, response.text) == (500, "Internal Server Error")
    assert db_engine.get_engine().pool.checkedout() == 0


@contextmanager
def one_connection_pool(disposable_database, monkeypatch, wait_seconds):
    """The process engine, replaced by a one-connection pool with the given wait."""
    with verified_engine(
        disposable_database,
        DB_POOL_SIZE="1",
        DB_POOL_MAX_OVERFLOW="0",
        DB_POOL_TIMEOUT_SECONDS=str(wait_seconds),
    ) as engine:
        monkeypatch.setattr(db_engine, "_engine", engine)
        yield engine


@pytest.fixture
def one_connection_engine(disposable_database, monkeypatch):
    """A one-connection pool with a 1 s wait, for the exhausted-pool tests."""
    with one_connection_pool(disposable_database, monkeypatch, 1) as engine:
        yield engine


@pytest.fixture
def patient_one_connection_engine(disposable_database, monkeypatch):
    """A one-connection pool with a 5 s wait, so slow CI queries wait instead of failing."""
    with one_connection_pool(disposable_database, monkeypatch, 5) as engine:
        yield engine


@pytest.mark.parametrize("url", ["/patients", "/fhir/Patient", "/fhir/Patient/{n1}"])
def test_engine_an_exhausted_pool_gives_a_plain_500_then_recovers(
    client, seeded, one_connection_engine, url
):
    held = one_connection_engine.connect()
    try:
        started = time.monotonic()
        response = client.get(url.format(**seeded))
        waited = time.monotonic() - started
    finally:
        held.close()

    assert (response.status_code, response.text) == (500, "Internal Server Error")
    assert waited < 10
    assert client.get(url.format(**seeded)).status_code == 200
    assert one_connection_engine.pool.checkedout() == 0


def test_engine_concurrent_searches_take_a_one_connection_pool_in_turn(
    client, seeded, patient_one_connection_engine
):
    one_connection_engine = patient_one_connection_engine
    start = threading.Barrier(2, timeout=10)
    results, checkouts = [], []
    record_checkout = lambda *args: checkouts.append(None)  # noqa: E731
    event.listen(one_connection_engine, "checkout", record_checkout)

    def search():
        start.wait()
        results.append(client.get("/fhir/Patient", params={"family": "sm"}).json()["total"])

    threads = [threading.Thread(target=search) for _ in range(2)]
    for thread in threads:
        thread.start()
    try:
        for thread in threads:
            thread.join(10)
    finally:
        event.remove(one_connection_engine, "checkout", record_checkout)

    assert not any(thread.is_alive() for thread in threads)
    assert results == [3, 3]
    assert len(checkouts) == 4  # page and total, then identifiers, for each search
    assert one_connection_engine.pool.checkedout() == 0
