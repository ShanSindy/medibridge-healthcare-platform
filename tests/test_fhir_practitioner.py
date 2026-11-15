from fhir import provider_to_fhir


def test_provider_to_fhir_with_contact_information():
    provider = {
        "id": 1,
        "first_name": "Jane",
        "last_name": "Smith",
        "specialty": "Cardiology",
        "email": "jane@example.com",
        "phone": "555-0100",
    }

    resource = provider_to_fhir(provider)

    assert resource["resourceType"] == "Practitioner"
    assert resource["id"] == "1"
    assert resource["name"] == [
        {
            "use": "official",
            "family": "Smith",
            "given": ["Jane"],
        }
    ]
    assert resource["telecom"] == [
        {"system": "email", "value": "jane@example.com"},
        {"system": "phone", "value": "555-0100"},
    ]
    assert "specialty" not in resource


def test_provider_to_fhir_without_contact_information():
    provider = {
        "id": 2,
        "first_name": "John",
        "last_name": "Doe",
        "specialty": "Neurology",
        "email": None,
        "phone": None,
    }

    resource = provider_to_fhir(provider)

    assert resource["resourceType"] == "Practitioner"
    assert resource["id"] == "2"
    assert resource["name"][0]["family"] == "Doe"
    assert resource["name"][0]["given"] == ["John"]
    assert "telecom" not in resource
