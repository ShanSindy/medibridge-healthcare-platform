
from datetime import date
from fastapi.testclient import TestClient
from main import app
from models import Patient
from fhir import patient_to_fhir
from fhir import create_operation_outcome

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
        response = client.get("/fhir/Patient")

        assert response.status_code == 200

        result = response.json()

        assert result["resourceType"] == "Bundle"
        assert result["type"] == "searchset"
        assert result["total"] == len(result["entry"])

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

    assert len(search_parameters) == 2

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
