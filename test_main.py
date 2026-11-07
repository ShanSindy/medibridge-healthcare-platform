
from datetime import date
from fastapi.testclient import TestClient
from main import app
from models import Patient
from fhir import patient_to_fhir
from fhir import create_operation_outcome
from database import get_connection

client = TestClient(app)


def test_health_check():
    response = client.get("/health")

    assert response.status_code == 200

    assert response.json() == {
        "status": "healthy",
        "service": "MediBridge API"
    }

def test_home():
    response = client.get("/")

    assert response.status_code == 200

    assert response.json() == {
        "application": "MediBridge",
        "status": "running"
    }

def test_get_patients():
    response = client.get("/patients")

    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_create_patient():
    patient_data = {
        "first_name": "Test",
        "last_name": "Patient",
        "date_of_birth": "2000-01-01",
        "gender": "other"
    }

    patient_id = None

    try:
        response = client.post("/patients", json=patient_data)

        assert response.status_code == 201

        created_patient = response.json()
        patient_id = created_patient["id"]

        assert created_patient["first_name"] == "Test"
        assert created_patient["last_name"] == "Patient"

    finally:
        if patient_id is not None:
            client.delete(f"/patients/{patient_id}")

def test_get_patient_not_found():
    response = client.get("/patients/999999")

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Patient not found"
    }

def test_update_patient():
    patient_data = {
        "first_name": "Original",
        "last_name": "Patient",
        "date_of_birth": "2000-01-01",
        "gender": "other"
    }

    create_response = client.post("/patients", json=patient_data)
    assert create_response.status_code == 201

    patient_id = create_response.json()["id"]

    try:
        updated_data = {
            "first_name": "Updated",
            "last_name": "Patient",
            "date_of_birth": "2000-01-01",
            "gender": "other"
        }

        response = client.put(
            f"/patients/{patient_id}",
            json=updated_data
        )

        assert response.status_code == 200
        assert response.json()["first_name"] == "Updated"
        assert response.json()["id"] == patient_id

    finally:
        client.delete(f"/patients/{patient_id}")

def test_delete_patient():
    patient_data = {
        "first_name": "Delete",
        "last_name": "Test",
        "date_of_birth": "2000-01-01",
        "gender": "other"
    }

    create_response = client.post("/patients", json=patient_data)
    assert create_response.status_code == 201

    patient_id = create_response.json()["id"]

    try:
        delete_response = client.delete(f"/patients/{patient_id}")

        assert delete_response.status_code == 204

        get_response = client.get(f"/patients/{patient_id}")

        assert get_response.status_code == 404

    finally:
        client.delete(f"/patients/{patient_id}")

def test_database_isolation():
    from database import get_connection

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            database_name = cursor.fetchone()[0]

    assert database_name == "medibridge_test"


def test_create_patient_empty_first_name():
    patient_data = {
        "first_name": "",
        "last_name": "Patient",
        "date_of_birth": "2000-01-01",
        "gender": "other"
    }

    response = client.post("/patients", json=patient_data)

    if response.status_code == 201:
        patient_id = response.json()["id"]
        client.delete(f"/patients/{patient_id}")

    assert response.status_code == 422


def test_create_patient_whitespace_first_name():
    patient_data = {
        "first_name": "   ",
        "last_name": "Patient",
        "date_of_birth": "2000-01-01",
        "gender": "other"
    }

    response = client.post("/patients", json=patient_data)

    if response.status_code == 201:
        patient_id = response.json()["id"]
        client.delete(f"/patients/{patient_id}")

    assert response.status_code == 422

def test_create_patient_future_date_of_birth():
    patient_data = {
        "first_name": "Future",
        "last_name": "Patient",
        "date_of_birth": "2099-01-01",
        "gender": "other"
    }

    response = client.post("/patients", json=patient_data)

    if response.status_code == 201:
        patient_id = response.json()["id"]
        client.delete(f"/patients/{patient_id}")

    assert response.status_code == 422

def test_create_patient_invalid_gender():
    patient_data = {
        "first_name": "Test",
        "last_name": "Patient",
        "date_of_birth": "2000-01-01",
        "gender": "invalid_value"
    }

    response = client.post("/patients", json=patient_data)

    if response.status_code == 201:
        patient_id = response.json()["id"]
        client.delete(f"/patients/{patient_id}")

    assert response.status_code == 422

def test_patient_to_fhir():
    patient = Patient(
        id=1,
        first_name="Alex",
        last_name="Morgan",
        date_of_birth=date(1992, 4, 15),
        gender="male"
    )

    result = patient_to_fhir(patient)

    assert result == {
        "resourceType": "Patient",
        "id": "1",
        "name": [
            {
                "use": "official",
                "family": "Morgan",
                "given": ["Alex"]
            }
        ],
        "gender": "male",
        "birthDate": "1992-04-15"
    }

def test_get_fhir_patient():
    patient_data = {
        "first_name": "FHIR",
        "last_name": "Test",
        "date_of_birth": "1995-06-15",
        "gender": "female"
    }

    create_response = client.post("/patients", json=patient_data)
    assert create_response.status_code == 201

    patient_id = create_response.json()["id"]

    try:
        response = client.get(f"/fhir/Patient/{patient_id}")

        assert response.status_code == 200

        result = response.json()

        assert result["resourceType"] == "Patient"
        assert result["id"] == str(patient_id)
        assert result["name"][0]["family"] == "Test"
        assert result["name"][0]["given"] == ["FHIR"]
        assert result["gender"] == "female"
        assert result["birthDate"] == "1995-06-15"

    finally:
        client.delete(f"/patients/{patient_id}")


def test_get_fhir_patient_not_found():
    response = client.get("/fhir/Patient/999999")

    assert response.status_code == 404

    assert response.json() == {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": "error",
                "code": "not-found",
                "diagnostics": "Patient not found"
            }
        ]
    }

    assert response.headers["content-type"] == "application/fhir+json"



def test_get_fhir_patients_bundle():
    patient_data = {
        "first_name": "Bundle",
        "last_name": "Test",
        "date_of_birth": "1998-03-20",
        "gender": "female"
    }

    create_response = client.post("/patients", json=patient_data)
    assert create_response.status_code == 201

    patient_id = create_response.json()["id"]

    try:
        response = client.get(
            "/fhir/Patient",
            params={
                "family": "Test",
                "given": "Bundle"
            }
        )

        assert response.status_code == 200

        result = response.json()

        assert result["resourceType"] == "Bundle"
        assert result["type"] == "searchset"
        assert result["total"] >= len(result["entry"])
        assert len(result["entry"]) <= 10

        matching_entries = [
            entry
            for entry in result["entry"]
            if entry["resource"]["id"] == str(patient_id)
        ]

        assert len(matching_entries) == 1

        patient = matching_entries[0]["resource"]

        assert patient["resourceType"] == "Patient"
        assert patient["name"][0]["family"] == "Test"
        assert patient["name"][0]["given"] == ["Bundle"]
        assert patient["birthDate"] == "1998-03-20"

    finally:
        client.delete(f"/patients/{patient_id}")


def test_get_fhir_patients_empty_bundle(monkeypatch):
    class FakeCursor:
        def execute(self, query, params=None):
            pass

        def fetchall(self):
            return []

        def fetchone(self):
            return (0,)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    class FakeConnection:
        def cursor(self):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr(
        "main.get_connection",
        lambda: FakeConnection()
    )

    response = client.get("/fhir/Patient")

    assert response.status_code == 200

    assert response.json() == {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": 0,
        "link": [
            {
                "relation": "self",
                "url": "/fhir/Patient?_count=10&_offset=0"
            }
        ],
        "entry": []
    }



def test_search_fhir_patients_by_family():
    patient_data = {
        "first_name": "Search",
        "last_name": "UniqueFHIRFamily123",
        "date_of_birth": "1995-05-20",
        "gender": "female"
    }

    create_response = client.post("/patients", json=patient_data)
    assert create_response.status_code == 201

    patient_id = create_response.json()["id"]

    try:
        response = client.get(
            "/fhir/Patient",
            params={"family": "uniquefhirfamily123"}
        )

        assert response.status_code == 200

        result = response.json()

        assert result["resourceType"] == "Bundle"
        assert result["type"] == "searchset"
        assert result["total"] == 1
        assert len(result["entry"]) == 1

        patient = result["entry"][0]["resource"]

        assert patient["id"] == str(patient_id)
        assert patient["name"][0]["family"] == "UniqueFHIRFamily123"
        assert patient["name"][0]["given"] == ["Search"]

    finally:
        client.delete(f"/patients/{patient_id}")

def test_search_fhir_patients_no_match():
    response = client.get(
        "/fhir/Patient",
        params={"family": "NonexistentFamily999"}
    )

    assert response.status_code == 200

    result = response.json()

   
    assert result == {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": 0,
        "link": [
            {
                "relation": "self",
                "url": "/fhir/Patient?family=NonexistentFamily999&_count=10&_offset=0"
            }
        ],
        "entry": []
    }


def test_search_fhir_patients_by_given():
    patient_data = {
        "first_name": "UniqueFHIRGiven123",
        "last_name": "Johnson",
        "date_of_birth": "1995-05-20",
        "gender": "female"
    }

    create_response = client.post("/patients", json=patient_data)
    assert create_response.status_code == 201

    patient_id = create_response.json()["id"]

    try:
        response = client.get(
            "/fhir/Patient",
            params={"given": "uniquefhirgiven123"}
        )

        assert response.status_code == 200

        result = response.json()

        assert result["resourceType"] == "Bundle"
        assert result["type"] == "searchset"
        assert result["total"] == 1
        assert len(result["entry"]) == 1

        patient = result["entry"][0]["resource"]

        assert patient["id"] == str(patient_id)
        assert patient["name"][0]["given"] == ["UniqueFHIRGiven123"]
        assert patient["name"][0]["family"] == "Johnson"

    finally:
        client.delete(f"/patients/{patient_id}")

def test_search_fhir_patients_by_family_and_given():
    patient_data = {
        "first_name": "UniqueCombinedGiven123",
        "last_name": "UniqueCombinedFamily123",
        "date_of_birth": "1994-08-12",
        "gender": "male"
    }

    create_response = client.post("/patients", json=patient_data)
    assert create_response.status_code == 201

    patient_id = create_response.json()["id"]

    try:
        response = client.get(
            "/fhir/Patient",
            params={
                "family": "uniquecombinedfamily123",
                "given": "uniquecombinedgiven123"
            }
        )

        assert response.status_code == 200

        result = response.json()

        assert result["resourceType"] == "Bundle"
        assert result["type"] == "searchset"
        assert result["total"] == 1
        assert len(result["entry"]) == 1

        patient = result["entry"][0]["resource"]

        assert patient["id"] == str(patient_id)
        assert patient["name"][0]["family"] == "UniqueCombinedFamily123"
        assert patient["name"][0]["given"] == ["UniqueCombinedGiven123"]

    finally:
        client.delete(f"/patients/{patient_id}")

def test_fhir_patient_count_pagination():
    patient_ids = []

    try:
        for i in range(3):
            response = client.post(
                "/patients",
                json={
                    "first_name": f"Pagination{i}",
                    "last_name": "UniquePaginationTest174",
                    "date_of_birth": "1995-05-20",
                    "gender": "female"
                }
            )

            assert response.status_code == 201
            patient_ids.append(response.json()["id"])

        response = client.get(
            "/fhir/Patient",
            params={
                "family": "UniquePaginationTest174",
                "_count": 2
            }
        )

        assert response.status_code == 200

        data = response.json()

        assert data["resourceType"] == "Bundle"
        assert data["type"] == "searchset"
        assert len(data["entry"]) == 2

    finally:
        for patient_id in patient_ids:
            client.delete(f"/patients/{patient_id}")

def test_fhir_patient_offset_pagination():
    patient_ids = []
    family = "UniqueOffsetTest177"

    try:
        for i in range(3):
            response = client.post(
                "/patients",
                json={
                    "first_name": f"OffsetPatient{i}",
                    "last_name": family,
                    "date_of_birth": "1995-05-20",
                    "gender": "female"
                }
            )

            assert response.status_code == 201
            patient_ids.append(response.json()["id"])

        # First page
        first_response = client.get(
            "/fhir/Patient",
            params={
                "family": family,
                "_count": 2,
                "_offset": 0
            }
        )

        assert first_response.status_code == 200
        first_entries = first_response.json()["entry"]

        assert len(first_entries) == 2

        # Second page
        second_response = client.get(
            "/fhir/Patient",
            params={
                "family": family,
                "_count": 2,
                "_offset": 2
            }
        )

        assert second_response.status_code == 200
        second_entries = second_response.json()["entry"]

        assert len(second_entries) == 1

        # Verify correct records and ordering
        first_ids = [
            entry["resource"]["id"]
            for entry in first_entries
        ]

        second_ids = [
            entry["resource"]["id"]
            for entry in second_entries
        ]

        assert first_ids == [
            str(patient_ids[0]),
            str(patient_ids[1])
        ]

        assert second_ids == [str(patient_ids[2])]

    finally:
        for patient_id in patient_ids:
            client.delete(f"/patients/{patient_id}")

def test_fhir_patient_total_across_pages():
    patient_ids = []
    family = "UniqueTotalTest180"

    try:
        for i in range(3):
            response = client.post(
                "/patients",
                json={
                    "first_name": f"TotalPatient{i}",
                    "last_name": family,
                    "date_of_birth": "1995-05-20",
                    "gender": "female"
                }
            )

            assert response.status_code == 201
            patient_ids.append(response.json()["id"])

        # Request the first page
        first_response = client.get(
            "/fhir/Patient",
            params={
                "family": family,
                "_count": 2,
                "_offset": 0
            }
        )

        assert first_response.status_code == 200
        first_data = first_response.json()

        assert first_data["total"] == 3
        assert len(first_data["entry"]) == 2

        # Request the second page
        second_response = client.get(
            "/fhir/Patient",
            params={
                "family": family,
                "_count": 2,
                "_offset": 2
            }
        )

        assert second_response.status_code == 200
        second_data = second_response.json()

        assert second_data["total"] == 3
        assert len(second_data["entry"]) == 1

    finally:
        for patient_id in patient_ids:
            client.delete(f"/patients/{patient_id}")

def test_fhir_patient_pagination_links():
    patient_ids = []
    family = "UniqueLinksTest182"

    try:
        for i in range(3):
            response = client.post(
                "/patients",
                json={
                    "first_name": f"LinkPatient{i}",
                    "last_name": family,
                    "date_of_birth": "1995-05-20",
                    "gender": "female"
                }
            )

            assert response.status_code == 201
            patient_ids.append(response.json()["id"])

        # First page
        first_response = client.get(
            "/fhir/Patient",
            params={
                "family": family,
                "_count": 2,
                "_offset": 0
            }
        )

        assert first_response.status_code == 200
        first_data = first_response.json()

        first_links = {
            link["relation"]: link["url"]
            for link in first_data["link"]
        }

        assert first_links["self"] == (
            f"/fhir/Patient?family={family}&_count=2&_offset=0"
        )

        assert first_links["next"] == (
            f"/fhir/Patient?family={family}&_count=2&_offset=2"
        )

        assert "previous" not in first_links

        # Second page
        second_response = client.get(
            "/fhir/Patient",
            params={
                "family": family,
                "_count": 2,
                "_offset": 2
            }
        )

        assert second_response.status_code == 200
        second_data = second_response.json()

        second_links = {
            link["relation"]: link["url"]
            for link in second_data["link"]
        }

        assert second_links["self"] == (
            f"/fhir/Patient?family={family}&_count=2&_offset=2"
        )

        assert second_links["previous"] == (
            f"/fhir/Patient?family={family}&_count=2&_offset=0"
        )

        assert "next" not in second_links

    finally:
        for patient_id in patient_ids:
            client.delete(f"/patients/{patient_id}")

def test_fhir_patient_invalid_pagination():
    invalid_cases = [
        {"_count": 0},
        {"_count": 101},
        {"_count": -1},
        {"_count": "abc"},
        {"_offset": -1},
        {"_offset": "abc"}
    ]

    for params in invalid_cases:
        response = client.get(
            "/fhir/Patient",
            params=params
        )

        assert response.status_code == 422, (
            f"Expected 422 for {params}, "
            f"got {response.status_code}"
        )

def test_fhir_patient_bundle_full_url():
    patient_id = None

    try:
        # Create a test patient
        response = client.post(
            "/patients",
            json={
                "first_name": "FullUrlTest",
                "last_name": "UniqueFullUrl187",
                "date_of_birth": "1995-05-20",
                "gender": "female"
            }
        )

        assert response.status_code == 201
        patient_id = response.json()["id"]

        # Search for the test patient
        response = client.get(
            "/fhir/Patient",
            params={"family": "UniqueFullUrl187"}
        )

        assert response.status_code == 200

        data = response.json()

        assert data["resourceType"] == "Bundle"
        assert data["total"] == 1
        assert len(data["entry"]) == 1

        # Verify fullUrl
        entry = data["entry"][0]

        expected_url = (
            f"http://testserver/fhir/Patient/{patient_id}"
        )

        assert entry["fullUrl"] == expected_url
        assert entry["resource"]["id"] == str(patient_id)

    finally:
        if patient_id is not None:
            client.delete(f"/patients/{patient_id}")

def test_create_operation_outcome():
    outcome = create_operation_outcome(
        code="not-found",
        diagnostics="Patient not found"
    )

    assert outcome == {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": "error",
                "code": "not-found",
                "diagnostics": "Patient not found"
            }
        ]
    }

def test_fhir_invalid_pagination_operation_outcome():
    response = client.get("/fhir/Patient?_count=0")

    assert response.status_code == 422

    assert response.headers["content-type"] == "application/fhir+json"

    assert response.json() == {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": "error",
                "code": "invalid",
                "diagnostics": "Invalid FHIR request parameters"
            }
        ]
    }

def test_regular_patient_validation_error():
    response = client.get("/patients/abc")

    assert response.status_code == 422

    assert response.headers["content-type"] == "application/json"

    data = response.json()

    assert "detail" in data
    assert isinstance(data["detail"], list)

    assert data["detail"][0]["loc"] == [
        "path",
        "patient_id"
    ]

def test_fhir_capability_statement():
    response = client.get("/fhir/metadata")

    assert response.status_code == 200

    data = response.json()

    assert data["resourceType"] == "CapabilityStatement"
    
    assert data["name"] == "MediBridgeCapabilityStatement"

    assert data["title"] == (
        "MediBridge FHIR R4 Capability Statement"
    )

    assert data["status"] == "active"
    assert data["publisher"] == "MediBridge Healthcare Platform"
    assert data["description"] == (
        "MediBridge is a healthcare interoperability platform "
        "providing FHIR R4 Patient resource access and search."
    )
    assert data["kind"] == "instance"
    assert data["fhirVersion"] == "4.0.1"
    assert "application/fhir+json" in data["format"]
    assert data["date"] == "2026-11-06"
    assert data["software"]["name"] == "MediBridge Healthcare Platform"
    assert data["software"]["version"] == "0.1.0"
    assert data["implementation"]["description"] == "MediBridge FHIR API"
    assert data["rest"][0]["resource"][0]["profile"] == (
        "http://hl7.org/fhir/StructureDefinition/Patient"
    )


    rest = data["rest"][0]

    assert rest["mode"] == "server"

    patient_resource = rest["resource"][0]

    assert patient_resource["type"] == "Patient"

    interactions = [
        interaction["code"]
        for interaction in patient_resource["interaction"]
    ]

    assert "read" in interactions
    assert "search-type" in interactions

def test_fhir_capability_statement_search_parameters():
    response = client.get("/fhir/metadata")

    assert response.status_code == 200

    data = response.json()

    patient_resource = data["rest"][0]["resource"][0]

    search_parameters = patient_resource["searchParam"]

    assert len(search_parameters) == 3

    parameters = {
        parameter["name"]: parameter
        for parameter in search_parameters
    }

    assert "family" in parameters
    assert "given" in parameters

    assert parameters["family"]["type"] == "string"
    assert parameters["given"]["type"] == "string"

    assert parameters["family"]["documentation"] == (
        "Search patients by family name."
    )

    assert parameters["given"]["documentation"] == (
        "Search patients by given name."
    )

def test_fhir_patient_family_prefix_search():
    # Create a patient with a known family name.
    create_response = client.post(
        "/patients",
        json={
            "first_name": "Michael",
            "last_name": "Johnson",
            "date_of_birth": "1995-05-15",
            "gender": "male"
        }
    )

    assert create_response.status_code in (200, 201)

    patient_id = create_response.json()["id"]

    # Search using only the beginning of the family name.
    response = client.get("/fhir/Patient?family=jOhN")

    assert response.status_code == 200

    data = response.json()

    assert data["resourceType"] == "Bundle"
    assert data["type"] == "searchset"

    matching_ids = [
        entry["resource"]["id"]
        for entry in data["entry"]
    ]

    assert str(patient_id) in [str(value) for value in matching_ids]

def test_fhir_patient_given_prefix_search():
    # Create a patient with a known given name.
    create_response = client.post(
        "/patients",
        json={
            "first_name": "Alexander",
            "last_name": "Williams",
            "date_of_birth": "1996-08-20",
            "gender": "male"
        }
    )

    assert create_response.status_code in (200, 201)

    patient_id = create_response.json()["id"]

    # Search using a partial, mixed-case given name.
    response = client.get("/fhir/Patient?given=aleX")

    assert response.status_code == 200

    data = response.json()

    assert data["resourceType"] == "Bundle"
    assert data["type"] == "searchset"

    matching_ids = [
        entry["resource"]["id"]
        for entry in data["entry"]
    ]

    assert str(patient_id) in [
        str(value) for value in matching_ids
    ]

def test_fhir_patient_search_escapes_wildcards():
    # Create a patient with a normal family name.
    create_response = client.post(
        "/patients",
        json={
            "first_name": "Daniel",
            "last_name": "Smith",
            "date_of_birth": "1994-06-12",
            "gender": "male"
        }
    )

    assert create_response.status_code in (200, 201)

    patient_id = create_response.json()["id"]

    # Percent sign must not behave as a wildcard.
    percent_response = client.get(
        "/fhir/Patient",
        params={"family": "Sm%"}
    )

    assert percent_response.status_code == 200

    percent_ids = [
        str(entry["resource"]["id"])
        for entry in percent_response.json()["entry"]
    ]

    assert str(patient_id) not in percent_ids

    # Underscore must not behave as a wildcard.
    underscore_response = client.get(
        "/fhir/Patient",
        params={"family": "Sm_th"}
    )

    assert underscore_response.status_code == 200

    underscore_ids = [
        str(entry["resource"]["id"])
        for entry in underscore_response.json()["entry"]
    ]

    assert str(patient_id) not in underscore_ids


def test_fhir_patient_includes_identifier():
    from database import get_connection

    patient_id = None

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO patients
                        (first_name, last_name, date_of_birth, gender)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    ("Identifier", "Test", "1995-06-15", "female")
                )
                patient_id = cursor.fetchone()[0]

                cursor.execute(
                    """
                    INSERT INTO patient_identifiers
                        (patient_id, system, value)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        patient_id,
                        "https://medibridge.example.org/mrn",
                        f"TEST-MRN-{patient_id}"
                    )
                )

        response = client.get(f"/fhir/Patient/{patient_id}")

        assert response.status_code == 200

        data = response.json()

        assert data["resourceType"] == "Patient"
        assert data["id"] == str(patient_id)
        assert data["identifier"] == [
            {
                "system": "https://medibridge.example.org/mrn",
                "value": f"TEST-MRN-{patient_id}"
            }
        ]

    finally:
        if patient_id is not None:
            with get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM patients WHERE id = %s",
                        (patient_id,)
                    )

                    
def test_fhir_patient_search_includes_identifier():
    from database import get_connection

    patient_id = None

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO patients
                        (first_name, last_name, date_of_birth, gender)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id
                    """,
                    ("BundleMRN", "SearchTest", "1995-06-15", "female")
                )

                patient_id = cursor.fetchone()[0]

                cursor.execute(
                    """
                    INSERT INTO patient_identifiers
                        (patient_id, system, value)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        patient_id,
                        "https://medibridge.example.org/mrn",
                        f"TEST-BUNDLE-MRN-{patient_id}"
                    )
                )

        response = client.get(
            "/fhir/Patient",
            params={
                "family": "SearchTest",
                "given": "BundleMRN"
            }
        )

        assert response.status_code == 200

        data = response.json()

        assert data["resourceType"] == "Bundle"
        assert data["type"] == "searchset"
        assert data["total"] == 1
        assert len(data["entry"]) == 1

        resource = data["entry"][0]["resource"]

        assert resource["id"] == str(patient_id)
        assert resource["identifier"] == [
            {
                "system": "https://medibridge.example.org/mrn",
                "value": f"TEST-BUNDLE-MRN-{patient_id}"
            }
        ]

    finally:
        if patient_id is not None:
            with get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM patients WHERE id = %s",
                        (patient_id,)
                    )

def test_fhir_patient_search_by_identifier():
    patient_data = {
        "first_name": "IdentifierSearch",
        "last_name": "TestPatient",
        "date_of_birth": "1996-08-12",
        "gender": "female"
    }

    create_response = client.post(
        "/patients",
        json=patient_data
    )
    assert create_response.status_code == 201

    patient_id = create_response.json()["id"]

    system = "https://medibridge.example.org/mrn"
    mrn = f"TEST-SEARCH-MRN-{patient_id}"

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO patient_identifiers
                        (patient_id, system, value)
                    VALUES (%s, %s, %s)
                    """,
                    (patient_id, system, mrn)
                )
            conn.commit()

        # Search using the MRN value alone.
        response = client.get(
            "/fhir/Patient",
            params={"identifier": mrn}
        )

        assert response.status_code == 200

        result = response.json()
        assert result["resourceType"] == "Bundle"
        assert result["type"] == "searchset"
        assert result["total"] == 1
        assert len(result["entry"]) == 1

        patient = result["entry"][0]["resource"]

        assert patient["id"] == str(patient_id)
        assert patient["identifier"] == [
            {"system": system, "value": mrn}
        ]

        # Search using the identifier system and value.
        response = client.get(
            "/fhir/Patient",
            params={
                "identifier": f"{system}|{mrn}"
            }
        )

        assert response.status_code == 200

        result = response.json()
        assert result["total"] == 1
        assert len(result["entry"]) == 1
        assert result["entry"][0]["resource"]["id"] == str(patient_id)

    finally:
        # Deleting the patient also deletes its identifiers
        # through the ON DELETE CASCADE foreign key.
        client.delete(f"/patients/{patient_id}")

def test_fhir_patient_search_identifier_no_match():
    # Search for an MRN that does not exist.
    response = client.get(
        "/fhir/Patient",
        params={
            "identifier": "NONEXISTENT-MRN-999999"
        }
    )

    assert response.status_code == 200

    result = response.json()

    assert result["resourceType"] == "Bundle"
    assert result["type"] == "searchset"
    assert result["total"] == 0
    assert result["entry"] == []

    # Search with an incorrect identifier system.
    response = client.get(
        "/fhir/Patient",
        params={
            "identifier": (
                "https://wrong.example.org/mrn"
                "|NONEXISTENT-MRN-999999"
            )
        }
    )

    assert response.status_code == 200

    result = response.json()

    assert result["total"] == 0
    assert result["entry"] == []

def test_fhir_patient_search_wrong_identifier_system():
    patient_data = {
        "first_name": "SystemCheck",
        "last_name": "TestPatient",
        "date_of_birth": "1994-05-18",
        "gender": "male"
    }

    create_response = client.post(
        "/patients",
        json=patient_data
    )
    assert create_response.status_code == 201

    patient_id = create_response.json()["id"]

    correct_system = "https://medibridge.example.org/mrn"
    wrong_system = "https://wrong.example.org/mrn"
    mrn = f"TEST-SYSTEM-MRN-{patient_id}"

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO patient_identifiers
                        (patient_id, system, value)
                    VALUES (%s, %s, %s)
                    """,
                    (patient_id, correct_system, mrn)
                )
            conn.commit()

        # The correct identifier system must find the patient.
        correct_response = client.get(
            "/fhir/Patient",
            params={
                "identifier": f"{correct_system}|{mrn}"
            }
        )

        assert correct_response.status_code == 200
        assert correct_response.json()["total"] == 1
        assert (
            correct_response.json()["entry"][0]["resource"]["id"]
            == str(patient_id)
        )

        # The wrong system must NOT find the same patient.
        wrong_response = client.get(
            "/fhir/Patient",
            params={
                "identifier": f"{wrong_system}|{mrn}"
            }
        )

        assert wrong_response.status_code == 200

        result = wrong_response.json()

        assert result["resourceType"] == "Bundle"
        assert result["type"] == "searchset"
        assert result["total"] == 0
        assert result["entry"] == []

    finally:
        client.delete(f"/patients/{patient_id}")

def test_fhir_patient_search_combined_name_and_identifier():
    patient_data = {
        "first_name": "CombinedSearch",
        "last_name": "IdentifierTest",
        "date_of_birth": "1993-07-21",
        "gender": "female"
    }

    create_response = client.post(
        "/patients",
        json=patient_data
    )
    assert create_response.status_code == 201

    patient_id = create_response.json()["id"]

    system = "https://medibridge.example.org/mrn"
    mrn = f"TEST-COMBINED-MRN-{patient_id}"

    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO patient_identifiers
                        (patient_id, system, value)
                    VALUES (%s, %s, %s)
                    """,
                    (patient_id, system, mrn)
                )
            conn.commit()

        # All three search filters match.
        response = client.get(
            "/fhir/Patient",
            params={
                "family": "IdentifierTest",
                "given": "CombinedSearch",
                "identifier": f"{system}|{mrn}"
            }
        )

        assert response.status_code == 200

        result = response.json()

        assert result["resourceType"] == "Bundle"
        assert result["type"] == "searchset"
        assert result["total"] == 1
        assert len(result["entry"]) == 1

        patient = result["entry"][0]["resource"]

        assert patient["id"] == str(patient_id)
        assert patient["name"][0]["family"] == "IdentifierTest"

        # An incorrect family name must return no matches.
        wrong_response = client.get(
            "/fhir/Patient",
            params={
                "family": "WrongFamily",
                "given": "CombinedSearch",
                "identifier": f"{system}|{mrn}"
            }
        )

        assert wrong_response.status_code == 200
        assert wrong_response.json()["total"] == 0
        assert wrong_response.json()["entry"] == []

    finally:
        client.delete(f"/patients/{patient_id}")

def test_fhir_metadata_advertises_identifier_search():
    response = client.get("/fhir/metadata")

    assert response.status_code == 200

    metadata = response.json()

    assert metadata["resourceType"] == "CapabilityStatement"
    assert metadata["fhirVersion"] == "4.0.1"

    patient_resource = next(
        resource
        for rest in metadata["rest"]
        for resource in rest["resource"]
        if resource["type"] == "Patient"
    )

    search_parameters = {
        param["name"]: param
        for param in patient_resource["searchParam"]
    }

    assert "family" in search_parameters
    assert "given" in search_parameters
    assert "identifier" in search_parameters

    assert search_parameters["family"]["type"] == "string"
    assert search_parameters["given"]["type"] == "string"
    assert search_parameters["identifier"]["type"] == "token"
