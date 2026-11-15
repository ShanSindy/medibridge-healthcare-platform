from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_fhir_metadata_returns_capability_statement():
    response = client.get("/fhir/metadata")

    assert response.status_code == 200

    metadata = response.json()
    assert metadata["resourceType"] == "CapabilityStatement"
    assert metadata["fhirVersion"] == "4.0.1"
    assert metadata["rest"][0]["mode"] == "server"


def test_fhir_metadata_advertises_patient_and_practitioner():
    response = client.get("/fhir/metadata")

    assert response.status_code == 200

    resources = {
        resource["type"]: resource
        for resource in response.json()["rest"][0]["resource"]
    }

    assert "Patient" in resources
    assert "Practitioner" in resources

    for resource_type in ("Patient", "Practitioner"):
        interactions = {
            interaction["code"]
            for interaction in resources[resource_type]["interaction"]
        }

        assert {"read", "search-type"} <= interactions


def test_fhir_metadata_advertises_search_parameters():
    response = client.get("/fhir/metadata")

    assert response.status_code == 200

    resources = {
        resource["type"]: resource
        for resource in response.json()["rest"][0]["resource"]
    }

    patient_params = {
        param["name"]
        for param in resources["Patient"]["searchParam"]
    }

    practitioner_params = {
        param["name"]
        for param in resources["Practitioner"]["searchParam"]
    }

    assert {"family", "given", "identifier"} <= patient_params
    assert {"family", "given"} <= practitioner_params

    assert resources["Practitioner"]["profile"] == (
        "http://hl7.org/fhir/StructureDefinition/Practitioner"
    )
